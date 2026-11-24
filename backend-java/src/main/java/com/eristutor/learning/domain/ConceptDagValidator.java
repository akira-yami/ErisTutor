package com.eristutor.learning.domain;

import com.eristutor.common.DomainValidationException;

import java.util.*;
import java.util.regex.Pattern;

public final class ConceptDagValidator {

    private static final Pattern ID_PATTERN = Pattern.compile("[A-Za-z0-9_.:/-]{1,120}");
    public static final int MAX_KNOWLEDGE_POINTS = 256;

    private ConceptDagValidator() {}

    public static String requireValidIdentifier(String value, String fieldName) {
        if (value == null || !ID_PATTERN.matcher(value).matches()) {
            throw new DomainValidationException(
                    "invalid_learning_identifier",
                    "Invalid identifier for " + fieldName + ": " + value
            );
        }
        return value;
    }

    public record KnowledgePointSpec(
            String kpId,
            String title,
            String kpType,
            List<String> prerequisites
    ) {}

    public static void validate(String pathId, List<KnowledgePointSpec> knowledgePoints) {
        requireValidIdentifier(pathId, "pathId");
        if (knowledgePoints == null || knowledgePoints.isEmpty()) {
            throw new DomainValidationException("empty_curriculum", "Learning path must contain at least one knowledge point");
        }
        if (knowledgePoints.size() > MAX_KNOWLEDGE_POINTS) {
            throw new DomainValidationException("curriculum_budget_exceeded", "Max 256 knowledge points allowed");
        }

        Map<String, KnowledgePointSpec> byId = new LinkedHashMap<>();
        for (KnowledgePointSpec kp : knowledgePoints) {
            requireValidIdentifier(kp.kpId(), "kpId");
            if (byId.containsKey(kp.kpId())) {
                throw new DomainValidationException("duplicate_skill", "Duplicate knowledge point: " + kp.kpId());
            }
            byId.put(kp.kpId(), kp);
        }

        Set<String> visiting = new HashSet<>();
        Set<String> visited = new HashSet<>();
        for (String kpId : byId.keySet()) {
            dfs(kpId, byId, visiting, visited);
        }
    }

    private static void dfs(
            String kpId,
            Map<String, KnowledgePointSpec> byId,
            Set<String> visiting,
            Set<String> visited
    ) {
        if (visiting.contains(kpId)) {
            throw new DomainValidationException("prerequisite_cycle", "Cycle detected involving " + kpId);
        }
        if (visited.contains(kpId)) {
            return;
        }
        visiting.add(kpId);
        List<String> prereqs = byId.get(kpId).prerequisites();
        if (prereqs != null) {
            for (String prereq : prereqs) {
                if (!byId.containsKey(prereq)) {
                    throw new DomainValidationException("unknown_prerequisite", "Unknown prerequisite: " + prereq);
                }
                dfs(prereq, byId, visiting, visited);
            }
        }
        visiting.remove(kpId);
        visited.add(kpId);
    }
}
