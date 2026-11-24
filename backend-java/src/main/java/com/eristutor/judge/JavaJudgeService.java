package com.eristutor.judge;

import com.eristutor.common.CanonicalJsonDigest;
import com.eristutor.common.DomainValidationException;
import com.eristutor.common.ErisTutorProperties;
import com.eristutor.common.NotFoundException;
import com.eristutor.learning.LearningCoreService;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

@Service
public class JavaJudgeService {

    public record InlineExercise(
            String className,
            String harness,
            List<KnowledgePackRegistry.TestCaseSpec> tests
    ) {}

    public record SubmitJavaRequest(
            String requestId,
            String packId,
            String nodeId,
            String exerciseId,
            String code,
            String assistance,
            Integer hintCount,
            Boolean answerRevealed,
            Double timeoutSeconds,
            Boolean commitToLearning,
            InlineExercise inlineExercise
    ) {}

    public record JudgeJobView(
            String jobId,
            String requestId,
            String packId,
            String nodeId,
            String exerciseId,
            String status,
            String result,
            String reason,
            String diagnostic,
            long durationMs,
            int exitCode,
            List<DockerJavaRunner.TestResult> tests,
            String attemptId,
            double createdAt,
            double completedAt,
            String guidance
    ) {}

    private final KnowledgePackRegistry packRegistry;
    private final DockerJavaRunner dockerRunner;
    private final JudgeSubmissionRepository submissionRepo;
    private final LearningCoreService learningService;
    private final ObjectMapper objectMapper;
    private final MeterRegistry meterRegistry;
    private final Semaphore concurrencyLimiter;
    private final ExecutorService virtualExecutor = Executors.newVirtualThreadPerTaskExecutor();
    private final ConcurrentMap<String, CompletableFuture<JudgeJobView>> activeFutures = new ConcurrentHashMap<>();
    private final ConcurrentMap<String, CopyOnWriteArrayList<SseEmitter>> emitters = new ConcurrentHashMap<>();

    public JavaJudgeService(
            KnowledgePackRegistry packRegistry,
            DockerJavaRunner dockerRunner,
            JudgeSubmissionRepository submissionRepo,
            LearningCoreService learningService,
            ObjectMapper objectMapper,
            ErisTutorProperties properties,
            MeterRegistry meterRegistry
    ) {
        this.packRegistry = packRegistry;
        this.dockerRunner = dockerRunner;
        this.submissionRepo = submissionRepo;
        this.learningService = learningService;
        this.objectMapper = objectMapper;
        this.meterRegistry = meterRegistry;
        int maxConc = properties.judge() != null ? Math.max(1, properties.judge().maxConcurrent()) : 2;
        this.concurrencyLimiter = new Semaphore(maxConc);
    }

    @PreDestroy
    public void shutdown() {
        virtualExecutor.shutdownNow();
    }

    public DockerJavaRunner.ReadinessStatus readiness() {
        return dockerRunner.checkReadiness();
    }

    public JudgeJobView submit(String userId, SubmitJavaRequest req, int waitSeconds) {
        if (req.code() == null || req.code().isBlank()) {
            throw new DomainValidationException("invalid_java_code_size", "Code is required");
        }
        String packId = (req.packId() == null || req.packId().isBlank()) ? "java-di" : req.packId().trim();
        Optional<JudgeSubmissionEntity> existing = submissionRepo.findByUserIdAndRequestId(userId, req.requestId());
        if (existing.isPresent()) {
            JudgeJobView existingView = toView(existing.get(), "");
            if ("running".equals(existingView.status()) && waitSeconds > 0) {
                return awaitFuture(existingView.jobId(), waitSeconds, existingView);
            }
            return existingView;
        }

        KnowledgePackRegistry.ExerciseSpec exercise = resolveExercise(packId, req);
        String jobId = "job_" + CanonicalJsonDigest.sha256(Map.of(
                "userId", userId,
                "requestId", req.requestId(),
                "ts", Instant.now().toEpochMilli()
        )).substring(0, 24);

        double now = Instant.now().toEpochMilli() / 1000.0;
        JudgeSubmissionEntity entity = new JudgeSubmissionEntity(
                jobId, userId, req.requestId(), packId, req.nodeId(), req.exerciseId(), "running", now
        );
        submissionRepo.save(entity);

        CompletableFuture<JudgeJobView> future = new CompletableFuture<>();
        activeFutures.put(jobId, future);

        virtualExecutor.submit(() -> executeAsync(userId, jobId, packId, exercise, req, future));

        JudgeJobView initial = toView(entity, "");
        if (waitSeconds > 0) {
            return awaitFuture(jobId, waitSeconds, initial);
        }
        return initial;
    }

    public JudgeJobView getJob(String userId, String jobId) {
        JudgeSubmissionEntity entity = submissionRepo.findByJobIdAndUserId(jobId, userId)
                .orElseThrow(() -> new NotFoundException("judge_job_not_found", "Judge job not found: " + jobId));
        return toView(entity, "");
    }

    public SseEmitter subscribe(String userId, String jobId) {
        JudgeJobView current = getJob(userId, jobId);
        SseEmitter emitter = new SseEmitter(60_000L);
        emitters.computeIfAbsent(jobId, k -> new CopyOnWriteArrayList<>()).add(emitter);
        emitter.onCompletion(() -> removeEmitter(jobId, emitter));
        emitter.onTimeout(() -> removeEmitter(jobId, emitter));
        emitter.onError(e -> removeEmitter(jobId, emitter));
        try {
            emitter.send(SseEmitter.event().name("judge").data(current));
            if (!"running".equals(current.status())) {
                emitter.complete();
            }
        } catch (IOException e) {
            removeEmitter(jobId, emitter);
        }
        return emitter;
    }

    private void executeAsync(
            String userId,
            String jobId,
            String packId,
            KnowledgePackRegistry.ExerciseSpec exercise,
            SubmitJavaRequest req,
            CompletableFuture<JudgeJobView> future
    ) {
        boolean acquired = false;
        try {
            concurrencyLimiter.acquire();
            acquired = true;
            DockerJavaRunner.ExecutionResult exec = dockerRunner.execute(req.code(), exercise, req.timeoutSeconds());
            meterRegistry.counter("eristutor_judge_runs_total", "status", exec.status(), "result", exec.result()).increment();

            String attemptId = "";
            boolean shouldCommit = !Boolean.FALSE.equals(req.commitToLearning()) && "completed".equals(exec.status());
            if (shouldCommit) {
                attemptId = commitLearningAttempt(userId, jobId, packId, req, exec);
            }

            double completedAt = Instant.now().toEpochMilli() / 1000.0;
            JudgeSubmissionEntity entity = submissionRepo.findById(jobId).orElseThrow();
            entity.setStatus(exec.status());
            entity.setResult(exec.result());
            entity.setReason(exec.reason());
            entity.setDiagnostic(exec.diagnostic());
            entity.setDurationMs(exec.durationMs());
            entity.setExitCode(exec.exitCode());
            entity.setTestsJson(objectMapper.writeValueAsString(exec.tests()));
            entity.setAttemptId(attemptId);
            entity.setCompletedAt(completedAt);
            submissionRepo.save(entity);

            JudgeJobView view = toView(entity, exec.guidance());
            future.complete(view);
            broadcast(jobId, view);
        } catch (Exception e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            JudgeSubmissionEntity entity = submissionRepo.findById(jobId).orElse(null);
            if (entity != null) {
                entity.setStatus("unavailable");
                entity.setReason("judge_worker_error");
                entity.setDiagnostic(e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName());
                entity.setCompletedAt(Instant.now().toEpochMilli() / 1000.0);
                submissionRepo.save(entity);
                JudgeJobView view = toView(entity, "Judge worker encountered an unexpected error.");
                future.complete(view);
                broadcast(jobId, view);
            } else {
                future.completeExceptionally(e);
            }
        } finally {
            if (acquired) {
                concurrencyLimiter.release();
            }
            activeFutures.remove(jobId);
        }
    }

    private String commitLearningAttempt(
            String userId, String jobId, String packId, SubmitJavaRequest req, DockerJavaRunner.ExecutionResult exec
    ) {
        String attemptId = "judge:" + jobId;
        double strength = exec.tests().isEmpty()
                ? ("correct".equals(exec.result()) ? 1.0 : 0.0)
                : (double) exec.tests().stream().filter(DockerJavaRunner.TestResult::passed).count() / exec.tests().size();
        String evidenceType = "incorrect".equals(exec.result()) ? "quiz_incorrect" : "application";
        String pathId = "";
        try {
            pathId = packRegistry.getPack(packId).pathId();
        } catch (NotFoundException ignored) {}

        learningService.recordAttempt(
                userId,
                new LearningCoreService.RecordAttemptRequest(
                        attemptId,
                        req.nodeId(),
                        exec.result(),
                        evidenceType,
                        strength,
                        "judge:" + req.requestId(),
                        "",
                        pathId,
                        req.exerciseId(),
                        req.exerciseId(),
                        jobId,
                        req.assistance(),
                        req.hintCount(),
                        req.answerRevealed(),
                        false,
                        "correct".equals(exec.result()) ? 3 : 1,
                        "correct".equals(exec.result()) ? "" : exec.reason(),
                        null
                )
        );
        return attemptId;
    }

    private KnowledgePackRegistry.ExerciseSpec resolveExercise(String packId, SubmitJavaRequest req) {
        if ("inline".equals(packId) && req.inlineExercise() != null) {
            InlineExercise inline = req.inlineExercise();
            return new KnowledgePackRegistry.ExerciseSpec(
                    req.exerciseId(),
                    "java",
                    "",
                    inline.className() != null ? inline.className() : "OrderService",
                    "",
                    inline.harness() != null ? inline.harness() : "",
                    inline.tests() != null ? inline.tests() : List.of(),
                    "",
                    ""
            );
        }
        return packRegistry.findExercise(packId, req.nodeId(), req.exerciseId());
    }

    private JudgeJobView awaitFuture(String jobId, int waitSeconds, JudgeJobView fallback) {
        CompletableFuture<JudgeJobView> future = activeFutures.get(jobId);
        if (future == null) {
            return submissionRepo.findById(jobId).map(e -> toView(e, "")).orElse(fallback);
        }
        try {
            return future.get(Math.min(25, Math.max(1, waitSeconds)), TimeUnit.SECONDS);
        } catch (Exception e) {
            return submissionRepo.findById(jobId).map(ent -> toView(ent, "")).orElse(fallback);
        }
    }

    private void broadcast(String jobId, JudgeJobView view) {
        List<SseEmitter> list = emitters.get(jobId);
        if (list == null) return;
        for (SseEmitter emitter : list) {
            try {
                emitter.send(SseEmitter.event().name("judge").data(view));
                emitter.complete();
            } catch (IOException e) {
                removeEmitter(jobId, emitter);
            }
        }
        emitters.remove(jobId);
    }

    private void removeEmitter(String jobId, SseEmitter emitter) {
        List<SseEmitter> list = emitters.get(jobId);
        if (list != null) {
            list.remove(emitter);
        }
    }

    private JudgeJobView toView(JudgeSubmissionEntity e, String guidance) {
        List<DockerJavaRunner.TestResult> tests = List.of();
        try {
            var type = objectMapper.getTypeFactory().constructCollectionType(List.class, DockerJavaRunner.TestResult.class);
            tests = objectMapper.readValue(e.getTestsJson(), type);
        } catch (JacksonException ignored) {}
        return new JudgeJobView(
                e.getJobId(), e.getRequestId(), e.getPackId(), e.getNodeId(), e.getExerciseId(),
                e.getStatus(), e.getResult(), e.getReason(), e.getDiagnostic(),
                e.getDurationMs(), e.getExitCode(), tests, e.getAttemptId(),
                e.getCreatedAt(), e.getCompletedAt(), guidance
        );
    }
}
