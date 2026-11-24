package com.eristutor.judge;

import com.eristutor.common.DomainValidationException;
import com.eristutor.common.ErisTutorProperties;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Component
public class DockerJavaRunner {

    public static final int MAX_CODE_BYTES = 12_000;
    public static final int MAX_OUTPUT_BYTES = 16_000;

    private static final Pattern CONTAINER_PATTERN = Pattern.compile("eris-java-[a-f0-9]{32}");
    private static final Pattern CLASS_NAME_PATTERN = Pattern.compile("[A-Za-z_$][A-Za-z0-9_$]{0,79}");

    public record ReadinessStatus(boolean available, String reason, String guidance, String image) {}

    public record TestResult(String id, String description, boolean passed) {}

    public record ExecutionResult(
            String status,
            String result,
            List<TestResult> tests,
            String reason,
            String diagnostic,
            long durationMs,
            String image,
            int exitCode,
            String guidance
    ) {}

    private final String dockerImage;
    private final int defaultTimeoutSeconds;
    private final Path workspaceRoot;

    public DockerJavaRunner(ErisTutorProperties properties) {
        this.dockerImage = properties.judge() != null && properties.judge().dockerImage() != null
                ? properties.judge().dockerImage()
                : "eclipse-temurin:21.0.8_9-jdk-jammy";
        this.defaultTimeoutSeconds = properties.judge() != null && properties.judge().timeoutSeconds() > 0
                ? properties.judge().timeoutSeconds()
                : 15;
        String wsDir = properties.judge() != null ? properties.judge().workspaceDir() : "";
        if (wsDir != null && !wsDir.isBlank()) {
            this.workspaceRoot = Path.of(wsDir).toAbsolutePath().normalize();
        } else {
            this.workspaceRoot = Path.of(System.getProperty("java.io.tmpdir"), "eristutor-java-judge");
        }
    }

    public ReadinessStatus checkReadiness() {
        String guidance = "Start Docker Desktop and pull JDK image: docker pull " + dockerImage;
        if (!runQuickCheck(List.of("docker", "info", "--format", "{{.ServerVersion}}"))) {
            return new ReadinessStatus(false, "docker_not_running", guidance, dockerImage);
        }
        if (!runQuickCheck(List.of("docker", "image", "inspect", dockerImage, "--format", "{{.Id}}"))) {
            return new ReadinessStatus(false, "jdk_image_missing", guidance, dockerImage);
        }
        return new ReadinessStatus(true, "ready", "", dockerImage);
    }

    public List<String> buildDockerCommand(Path folder, String containerName, String className, double timeoutSeconds) {
        if (!CONTAINER_PATTERN.matcher(containerName).matches()) {
            throw new DomainValidationException("invalid_java_container_name", "Invalid container name");
        }
        if (!CLASS_NAME_PATTERN.matcher(className).matches() || "Harness".equals(className)) {
            throw new DomainValidationException("invalid_java_class_name", "Invalid Java class name: " + className);
        }
        double bound = Math.min(30.0, Math.max(0.2, timeoutSeconds));
        String script = "mkdir -p /tmp/classes && "
                + "javac -J-Xmx128m -J-XX:ActiveProcessorCount=1 -J-XX:+UseSerialGC -encoding UTF-8 "
                + "-d /tmp/classes '/code/" + className + ".java' /code/Harness.java || exit 101; "
                + "printf '__ERIS_COMPILED__\\n'; "
                + "exec java -Xmx128m -XX:MaxMetaspaceSize=96m -XX:ActiveProcessorCount=1 -XX:+UseSerialGC "
                + "-Djava.io.tmpdir=/tmp -cp /tmp/classes Harness";

        return List.of(
                "docker", "run", "--rm", "--pull", "never", "--name", containerName,
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
                "--user", "65534:65534", "--log-driver", "none",
                "--ulimit", "fsize=16777216:16777216", "--ulimit", "nofile=128:128",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--mount", "type=bind,source=" + folder.resolve("code").toAbsolutePath() + ",target=/code,readonly",
                "--workdir", "/tmp", dockerImage,
                "timeout", "-k", "1s", String.format(Locale.US, "%gs", bound), "sh", "-c", script
        );
    }

    public ExecutionResult execute(String code, KnowledgePackRegistry.ExerciseSpec exercise, Double timeoutOverride) {
        if (code == null || code.isBlank() || code.getBytes(StandardCharsets.UTF_8).length > MAX_CODE_BYTES) {
            throw new DomainValidationException("invalid_java_code_size", "Java code must be non-empty and <= 12KB");
        }
        ReadinessStatus ready = checkReadiness();
        if (!ready.available()) {
            return new ExecutionResult(
                    "unavailable", "", List.of(), ready.reason(), "", 0, dockerImage, -1, ready.guidance()
            );
        }

        double bound = Math.min(30.0, Math.max(0.2, timeoutOverride != null ? timeoutOverride : defaultTimeoutSeconds));
        String containerName = "eris-java-" + UUID.randomUUID().toString().replace("-", "");
        Path jobDir = workspaceRoot.resolve(containerName);
        Path sourceDir = jobDir.resolve("code");

        List<String> command = buildDockerCommand(jobDir, containerName, exercise.className(), bound);
        long startNs = System.nanoTime();
        try {
            Files.createDirectories(sourceDir);
            Files.writeString(sourceDir.resolve(exercise.className() + ".java"), code, StandardCharsets.UTF_8);
            Files.writeString(sourceDir.resolve("Harness.java"), exercise.harness(), StandardCharsets.UTF_8);

            BoundedRunOutput run = boundedRun(command, bound + 2.0);
            long durationMs = Math.round((System.nanoTime() - startNs) / 1_000_000.0);

            if ((run.exitCode() == 125 || run.exitCode() == 126 || run.exitCode() == 127)
                    && !run.stdout().contains("__ERIS_COMPILED__")) {
                return new ExecutionResult(
                        "unavailable", "", List.of(), "docker_container_failed", "", durationMs,
                        dockerImage, run.exitCode(), "Check Docker Desktop / JDK image and bind mount permissions."
                );
            }

            String failure = run.failure();
            if ((run.exitCode() == 124 || run.exitCode() == 137) && failure.isBlank()) {
                failure = run.exitCode() == 124 ? "timeout" : "resource_limit";
            }

            List<TestResult> testResults = new ArrayList<>();
            int passedCount = 0;
            for (KnowledgePackRegistry.TestCaseSpec tc : exercise.tests()) {
                Pattern p = Pattern.compile("^" + Pattern.quote(tc.id()) + ":(PASS|FAIL)\\r?$", Pattern.MULTILINE);
                Matcher m = p.matcher(run.stdout());
                List<String> matches = new ArrayList<>();
                while (m.find()) {
                    matches.add(m.group(1));
                }
                boolean passed = matches.size() == 1 && "PASS".equals(matches.get(0)) && failure.isBlank();
                if (passed) passedCount++;
                testResults.add(new TestResult(tc.id(), tc.description(), passed));
            }

            String verdict = (passedCount == testResults.size() && run.exitCode() == 0 && failure.isBlank())
                    ? "correct"
                    : (passedCount > 0 ? "partial" : "incorrect");

            String reason;
            if (!failure.isBlank()) {
                reason = failure;
            } else if (run.exitCode() == 101 && !run.stdout().contains("__ERIS_COMPILED__")) {
                reason = "compile_error";
            } else if (run.exitCode() != 0) {
                reason = "runtime_error";
            } else {
                reason = "graded";
            }

            String diagnostic = "compile_error".equals(reason)
                    ? run.stderr().substring(0, Math.min(2000, run.stderr().length()))
                    : "";
            if (run.stderr().contains("Harness.java")) {
                diagnostic = "Check constructor signature, method parameters, and required interfaces in the prompt.";
            }

            return new ExecutionResult(
                    "completed", verdict, testResults, reason, diagnostic, durationMs, dockerImage, run.exitCode(), ""
            );
        } catch (IOException | InterruptedException e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            long durationMs = Math.round((System.nanoTime() - startNs) / 1_000_000.0);
            return new ExecutionResult(
                    "unavailable", "", List.of(), "docker_execution_failed", "", durationMs,
                    dockerImage, -1, "Verify Docker Desktop and JDK image."
            );
        } finally {
            forceRemoveContainer(containerName);
            deleteTree(jobDir);
        }
    }

    private record BoundedRunOutput(int exitCode, String stdout, String stderr, String failure) {}

    private BoundedRunOutput boundedRun(List<String> command, double timeoutSeconds)
            throws IOException, InterruptedException {
        Process process = new ProcessBuilder(command).start();
        ByteArrayOutputStream outBuf = new ByteArrayOutputStream();
        ByteArrayOutputStream errBuf = new ByteArrayOutputStream();
        AtomicBoolean overflow = new AtomicBoolean(false);

        Thread outReader = Thread.startVirtualThread(() -> readBounded(process.getInputStream(), outBuf, overflow));
        Thread errReader = Thread.startVirtualThread(() -> readBounded(process.getErrorStream(), errBuf, overflow));

        long deadlineNs = System.nanoTime() + (long) (timeoutSeconds * 1_000_000_000L);
        String failure = "";
        while (process.isAlive()) {
            if (overflow.get()) {
                failure = "output_limit";
                process.destroyForcibly();
                break;
            }
            if (System.nanoTime() >= deadlineNs) {
                failure = "timeout";
                process.destroyForcibly();
                break;
            }
            Thread.sleep(10);
        }
        process.waitFor(3, TimeUnit.SECONDS);
        outReader.join(500);
        errReader.join(500);
        if (overflow.get()) {
            failure = "output_limit";
        }
        return new BoundedRunOutput(
                process.exitValue(),
                outBuf.toString(StandardCharsets.UTF_8),
                errBuf.toString(StandardCharsets.UTF_8),
                failure
        );
    }

    private static void readBounded(InputStream is, ByteArrayOutputStream buf, AtomicBoolean overflow) {
        byte[] chunk = new byte[1024];
        try (is) {
            int n;
            while ((n = is.read(chunk)) != -1) {
                int room = MAX_OUTPUT_BYTES - buf.size();
                if (room > 0) {
                    buf.write(chunk, 0, Math.min(n, room));
                }
                if (n > room) {
                    overflow.set(true);
                }
            }
        } catch (IOException ignored) {}
    }

    private static boolean runQuickCheck(List<String> cmd) {
        try {
            Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
            try (InputStream is = p.getInputStream()) {
                is.readAllBytes();
            }
            boolean finished = p.waitFor(3, TimeUnit.SECONDS);
            if (!finished) {
                p.destroyForcibly();
                return false;
            }
            return p.exitValue() == 0;
        } catch (Exception e) {
            return false;
        }
    }

    private static void forceRemoveContainer(String containerName) {
        try {
            Process p = new ProcessBuilder("docker", "rm", "-f", containerName)
                    .redirectErrorStream(true)
                    .start();
            p.waitFor(3, TimeUnit.SECONDS);
        } catch (Exception ignored) {}
    }

    private static void deleteTree(Path dir) {
        try {
            if (Files.exists(dir)) {
                try (var walk = Files.walk(dir)) {
                    walk.sorted(Comparator.reverseOrder()).forEach(p -> {
                        try { Files.deleteIfExists(p); } catch (IOException ignored) {}
                    });
                }
            }
        } catch (IOException ignored) {}
    }
}
