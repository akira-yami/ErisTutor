package com.eristutor.judge;

import com.eristutor.common.ErisTutorProperties;
import com.eristutor.common.NotFoundException;
import jakarta.annotation.PostConstruct;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Stream;

@Component
public class KnowledgePackRegistry {

    public record TestCaseSpec(String id, String description) {}

    public record ExerciseSpec(
            String id,
            String type,
            String prompt,
            String className,
            String starterCode,
            String harness,
            List<TestCaseSpec> tests,
            String hint,
            String solution
    ) {}

    public record NodeSpec(
            String id,
            String title,
            String summary,
            List<String> prerequisites,
            List<ExerciseSpec> exercises
    ) {}

    public record PackSpec(
            String groupId,
            String pathId,
            String title,
            String goal,
            List<NodeSpec> nodes
    ) {}

    private final ErisTutorProperties properties;
    private final ObjectMapper objectMapper;
    private final Map<String, PackSpec> packs = new ConcurrentHashMap<>();

    public KnowledgePackRegistry(ErisTutorProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void loadPacks() {
        String dirCfg = properties.packs() != null ? properties.packs().dir() : null;
        if (dirCfg != null && !dirCfg.isBlank()) {
            Path dir = Path.of(dirCfg).toAbsolutePath().normalize();
            if (Files.isDirectory(dir)) {
                try (Stream<Path> files = Files.list(dir)) {
                    files.filter(p -> p.getFileName().toString().endsWith(".json"))
                            .forEach(p -> {
                                try (InputStream is = Files.newInputStream(p)) {
                                    registerFromStream(is);
                                } catch (IOException ignored) {}
                            });
                } catch (IOException ignored) {}
            }
        }
        if (!packs.containsKey("java-di")) {
            ClassPathResource fallback = new ClassPathResource("packs/java-di.json");
            if (fallback.exists()) {
                try (InputStream is = fallback.getInputStream()) {
                    registerFromStream(is);
                } catch (IOException ignored) {}
            }
        }
    }

    private void registerFromStream(InputStream is) {
        JsonNode root = objectMapper.readTree(is);
        String groupId = root.path("group_id").asText("");
        if (groupId.isBlank()) return;
        String pathId = root.path("path_id").asText(groupId);
        String title = root.path("title").asText(groupId);
        String goal = root.path("goal").asText("");
        List<NodeSpec> nodes = new ArrayList<>();
        for (JsonNode n : root.path("nodes")) {
            List<String> prereqs = new ArrayList<>();
            n.path("prerequisites").forEach(p -> prereqs.add(p.asText()));
            List<ExerciseSpec> exercises = new ArrayList<>();
            for (JsonNode ex : n.path("exercises")) {
                List<TestCaseSpec> tests = new ArrayList<>();
                for (JsonNode t : ex.path("tests")) {
                    tests.add(new TestCaseSpec(t.path("id").asText(), t.path("description").asText("")));
                }
                exercises.add(new ExerciseSpec(
                        ex.path("id").asText(),
                        ex.path("type").asText(),
                        ex.path("prompt").asText(""),
                        ex.path("class_name").asText("Solution"),
                        ex.path("starter_code").asText(""),
                        ex.path("harness").asText(""),
                        tests,
                        ex.path("hint").asText(""),
                        ex.path("solution").asText("")
                ));
            }
            nodes.add(new NodeSpec(
                    n.path("id").asText(),
                    n.path("title").asText(),
                    n.path("summary").asText(""),
                    prereqs,
                    exercises
            ));
        }
        packs.put(groupId, new PackSpec(groupId, pathId, title, goal, nodes));
    }

    public List<PackSpec> listPacks() {
        return packs.values().stream()
                .sorted(Comparator.comparing(PackSpec::groupId))
                .toList();
    }

    public PackSpec getPack(String groupId) {
        PackSpec spec = packs.get(groupId);
        if (spec == null) {
            throw new NotFoundException("pack_not_found", "Knowledge pack not found: " + groupId);
        }
        return spec;
    }

    public ExerciseSpec findExercise(String groupId, String nodeId, String exerciseId) {
        PackSpec pack = getPack(groupId);
        return pack.nodes().stream()
                .filter(n -> n.id().equals(nodeId))
                .flatMap(n -> n.exercises().stream())
                .filter(ex -> ex.id().equals(exerciseId))
                .findFirst()
                .orElseThrow(() -> new NotFoundException("exercise_not_found",
                        "Exercise not found: " + groupId + "/" + nodeId + "/" + exerciseId));
    }
}
