package com.eristutor.learning.domain;

import java.util.*;

public final class MasteryEngine {

    public static final double HALF_LIFE_SECONDS = 7.0 * 24.0 * 3600.0;
    public static final double EVIDENCE_WEIGHT_MIN = 0.10;
    public static final double EVIDENCE_WEIGHT_MAX = 1.00;
    public static final double CONFIDENCE_CAP = 0.95;
    public static final double MISCONCEPTION_DECAY = 0.05;

    private static final Map<String, Double> STRENGTH_MAP = Map.of(
            "quiz_correct", 1.00,
            "feynman_pass", 1.00,
            "application", 0.85,
            "paraphrase", 0.70,
            "quiz_incorrect", 0.00,
            "feynman_fail", 0.00,
            "misconception", 0.00,
            "self_report", 0.20,
            "exposure", 0.10
    );

    private static final Map<String, Double> MASTERY_THRESHOLDS = Map.of(
            "fact", 0.90,
            "procedure", 0.90,
            "concept", 0.75,
            "design", 0.70
    );

    private MasteryEngine() {}

    public record EvidenceObservation(
            String evidenceId,
            String kpId,
            String evidenceType,
            double strength,
            boolean correct,
            String interactionId,
            String turnId,
            double recordedAt,
            String errorCode,
            String result,
            String assistance,
            int hintCount,
            boolean answerRevealed,
            String itemFamilyId
    ) {
        public boolean isIndependent() {
            String effectiveAssistance = (assistance == null || assistance.isBlank()) ? "independent" : assistance;
            return "independent".equals(effectiveAssistance)
                    && !answerRevealed
                    && hintCount == 0
                    && !"partial".equals(result)
                    && !"ungraded".equals(result);
        }
    }

    public record MasteryResult(
            String kpId,
            double mastery,
            double confidence,
            String stage,
            int attemptCount,
            int correctCount,
            double lastEvidenceAt,
            List<String> errorCodes
    ) {}

    public static MasteryResult computeMastery(String kpId, List<EvidenceObservation> allEvidence, double nowSeconds) {
        List<EvidenceObservation> kpEvidence = allEvidence.stream()
                .filter(e -> kpId.equals(e.kpId()))
                .sorted(Comparator.comparingDouble(EvidenceObservation::recordedAt)
                        .thenComparing(EvidenceObservation::evidenceId))
                .toList();

        if (kpEvidence.isEmpty()) {
            return new MasteryResult(kpId, 0.0, 0.0, "learning", 0, 0, 0.0, List.of());
        }

        // Keep latest independent observation per itemFamilyId (or per evidenceId if no family)
        Map<String, EvidenceObservation> scored = new LinkedHashMap<>();
        for (EvidenceObservation ev : kpEvidence) {
            if (ev.isIndependent()) {
                String family = (ev.itemFamilyId() != null && !ev.itemFamilyId().isBlank())
                        ? "family:" + ev.itemFamilyId()
                        : "evidence:" + ev.evidenceId();
                scored.put(family, ev);
            }
        }
        Set<String> scoredIds = new HashSet<>();
        for (EvidenceObservation ev : scored.values()) {
            scoredIds.add(ev.evidenceId());
        }

        double weightedSum = 0.0;
        double weightTotal = 0.0;
        int attemptCount = 0;
        int independentAttempts = 0;
        int correctCount = 0;
        int misconceptionCount = 0;
        double lastAt = 0.0;
        Set<String> successFamilies = new HashSet<>();
        Set<String> successTurns = new HashSet<>();
        List<String> errorCodes = new ArrayList<>();

        for (EvidenceObservation ev : kpEvidence) {
            double age = Math.max(0.0, nowSeconds - ev.recordedAt());
            double w = Math.exp(-Math.log(2.0) * age / HALF_LIFE_SECONDS);
            w = Math.max(EVIDENCE_WEIGHT_MIN, Math.min(EVIDENCE_WEIGHT_MAX, w));

            double baseStrength = STRENGTH_MAP.getOrDefault(ev.evidenceType(), 0.0);
            double effectiveStrength = baseStrength > 0.0
                    ? baseStrength * Math.max(0.0, Math.min(1.0, ev.strength()))
                    : 0.0;
            if (!ev.isIndependent()) {
                effectiveStrength = 0.0;
            }

            boolean eligible = scoredIds.contains(ev.evidenceId());
            if (eligible) {
                weightedSum += w * effectiveStrength;
                weightTotal += w;
            }

            if (!"exposure".equals(ev.evidenceType()) && !"self_report".equals(ev.evidenceType())) {
                attemptCount++;
                if (eligible) {
                    independentAttempts++;
                }
                String family = firstNonBlank(ev.itemFamilyId(), ev.interactionId(), ev.turnId(), ev.evidenceId());
                boolean turnUnused = ev.turnId() == null || ev.turnId().isBlank() || !successTurns.contains(ev.turnId());
                if (ev.correct() && eligible && !successFamilies.contains(family) && turnUnused) {
                    correctCount++;
                    successFamilies.add(family);
                    if (ev.turnId() != null && !ev.turnId().isBlank()) {
                        successTurns.add(ev.turnId());
                    }
                }
            }

            if ("misconception".equals(ev.evidenceType()) && eligible) {
                misconceptionCount++;
                if (ev.errorCode() != null && !ev.errorCode().isBlank()) {
                    errorCodes.add(ev.errorCode());
                }
            }

            lastAt = Math.max(lastAt, ev.recordedAt());
        }

        double raw = weightTotal > 0.0 ? (weightedSum / weightTotal) : 0.0;
        for (int i = 0; i < misconceptionCount; i++) {
            raw -= MISCONCEPTION_DECAY * Math.max(0.0, raw);
        }
        double mastery = Math.max(0.0, Math.min(CONFIDENCE_CAP, raw));

        List<EvidenceObservation> sortedScored = scored.values().stream()
                .sorted(Comparator.comparingDouble(EvidenceObservation::recordedAt))
                .toList();
        int fromIdx = Math.max(0, sortedScored.size() - 5);
        Set<String> recentFamilies = new HashSet<>();
        for (EvidenceObservation ev : sortedScored.subList(fromIdx, sortedScored.size())) {
            if (ev.correct() && ev.isIndependent() && (
                    "quiz_correct".equals(ev.evidenceType())
                            || "feynman_pass".equals(ev.evidenceType())
                            || "application".equals(ev.evidenceType()))) {
                recentFamilies.add(firstNonBlank(ev.itemFamilyId(), ev.interactionId(), ev.turnId(), ev.evidenceId()));
            }
        }
        double confidence = Math.min(1.0, recentFamilies.size() / 5.0);
        String stage = assignStage(mastery, independentAttempts, correctCount);

        LinkedHashSet<String> uniqueRecentErrors = new LinkedHashSet<>(
                errorCodes.subList(Math.max(0, errorCodes.size() - 8), errorCodes.size())
        );

        return new MasteryResult(
                kpId,
                round4(mastery),
                round4(confidence),
                stage,
                attemptCount,
                correctCount,
                lastAt,
                new ArrayList<>(uniqueRecentErrors)
        );
    }

    public static String assignStage(double mastery, int independentAttempts, int correctCount) {
        if (mastery >= 0.90 && independentAttempts >= 3 && correctCount >= 3) {
            return "retained";
        }
        if (mastery >= 0.70 && independentAttempts >= 2) {
            return "mastered";
        }
        if (mastery >= 0.40 || independentAttempts >= 1) {
            return "practicing";
        }
        return "learning";
    }

    public static boolean gatePassed(MasteryResult record, String kpType, boolean hasFeynmanPass) {
        double threshold = MASTERY_THRESHOLDS.getOrDefault(kpType, 0.75);
        if (record.mastery() < threshold) {
            return false;
        }
        if ("fact".equals(kpType) || "procedure".equals(kpType)) {
            return record.correctCount() >= 2;
        }
        return hasFeynmanPass;
    }

    private static double round4(double v) {
        return Math.round(v * 10000.0) / 10000.0;
    }

    private static String firstNonBlank(String... values) {
        for (String v : values) {
            if (v != null && !v.isBlank()) {
                return v;
            }
        }
        return "";
    }
}
