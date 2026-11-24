package com.eristutor.learning;

import com.eristutor.common.CanonicalJsonDigest;
import com.eristutor.common.ConflictException;
import com.eristutor.common.DomainValidationException;
import com.eristutor.common.NotFoundException;
import com.eristutor.learning.domain.ConceptDagValidator;
import com.eristutor.learning.domain.MasteryEngine;
import com.eristutor.learning.domain.ReviewScheduler;
import com.eristutor.learning.persistence.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.*;

@Service
public class LearningCoreService {

    private static final Set<String> VALID_RESULTS = Set.of("correct", "incorrect", "partial", "ungraded");
    private static final Set<String> VALID_ASSISTANCE = Set.of("independent", "hinted", "worked_with_eris", "answer_revealed");
    private static final Set<String> POSITIVE_EVIDENCE = Set.of("quiz_correct", "feynman_pass", "application", "paraphrase");
    private static final Set<String> NEGATIVE_EVIDENCE = Set.of("quiz_incorrect", "feynman_fail", "misconception");

    private final LearningPathRepository pathRepo;
    private final KnowledgePointRepository kpRepo;
    private final LearningPathRevisionRepository revisionRepo;
    private final LearningEvidenceRepository evidenceRepo;
    private final LearningAttemptRepository attemptRepo;
    private final LearningAssistanceRepository assistanceRepo;
    private final LearningForgetTombstoneRepository tombstoneRepo;
    private final LearningForgottenInteractionRepository forgottenInteractionRepo;
    private final MasteryRecordRepository masteryRepo;
    private final ReviewItemRepository reviewRepo;
    private final ObjectMapper objectMapper;

    public LearningCoreService(
            LearningPathRepository pathRepo,
            KnowledgePointRepository kpRepo,
            LearningPathRevisionRepository revisionRepo,
            LearningEvidenceRepository evidenceRepo,
            LearningAttemptRepository attemptRepo,
            LearningAssistanceRepository assistanceRepo,
            LearningForgetTombstoneRepository tombstoneRepo,
            LearningForgottenInteractionRepository forgottenInteractionRepo,
            MasteryRecordRepository masteryRepo,
            ReviewItemRepository reviewRepo,
            ObjectMapper objectMapper
    ) {
        this.pathRepo = pathRepo;
        this.kpRepo = kpRepo;
        this.revisionRepo = revisionRepo;
        this.evidenceRepo = evidenceRepo;
        this.attemptRepo = attemptRepo;
        this.assistanceRepo = assistanceRepo;
        this.tombstoneRepo = tombstoneRepo;
        this.forgottenInteractionRepo = forgottenInteractionRepo;
        this.masteryRepo = masteryRepo;
        this.reviewRepo = reviewRepo;
        this.objectMapper = objectMapper;
    }

    public record KpInput(String kpId, String title, String kpType, List<String> prerequisites) {}

    public record RegisterPathRequest(
            String pathId,
            String title,
            String goal,
            String domainId,
            String source,
            String expectedRevision,
            String reason,
            List<KpInput> knowledgePoints
    ) {}

    public record PathResponse(
            String pathId,
            String title,
            String goal,
            String domainId,
            String source,
            String revision,
            long version,
            List<KpInput> knowledgePoints,
            double updatedAt
    ) {}

    public record RecordAttemptRequest(
            String attemptId,
            String kpId,
            String result,
            String evidenceType,
            Double strength,
            String interactionId,
            String turnId,
            String pathId,
            String itemId,
            String itemFamilyId,
            String activityId,
            String assistance,
            Integer hintCount,
            Boolean answerRevealed,
            Boolean reviewRecall,
            Integer rating,
            String errorCode,
            Double recordedAt
    ) {}

    public record CorrectAttemptRequest(
            String correctionId,
            String result,
            String evidenceType,
            Double strength,
            String errorCode
    ) {}

    public record AttemptReceipt(
            String status,
            boolean idempotentReplay,
            String attemptId,
            String evidenceId,
            String kpId,
            String result,
            String assistance,
            MasteryEngine.MasteryResult mastery
    ) {}

    @Transactional
    public PathResponse registerOrRevisePath(String userId, RegisterPathRequest req) {
        List<ConceptDagValidator.KnowledgePointSpec> specs = req.knowledgePoints() == null ? List.of() :
                req.knowledgePoints().stream()
                        .map(k -> new ConceptDagValidator.KnowledgePointSpec(
                                k.kpId(),
                                k.title() != null ? k.title() : k.kpId(),
                                k.kpType() != null && !k.kpType().isBlank() ? k.kpType() : "concept",
                                k.prerequisites() != null ? k.prerequisites() : List.of()
                        ))
                        .toList();
        ConceptDagValidator.validate(req.pathId(), specs);

        double now = nowSeconds();
        String domainId = nonNull(req.domainId());
        String source = nonNullOr(req.source(), "user");
        String goal = nonNull(req.goal());
        String title = nonNullOr(req.title(), req.pathId());

        Map<String, Object> canonicalMap = new LinkedHashMap<>();
        canonicalMap.put("pathId", req.pathId());
        canonicalMap.put("title", title);
        canonicalMap.put("goal", goal);
        canonicalMap.put("domainId", domainId);
        canonicalMap.put("knowledgePoints", specs);
        String nextRevision = CanonicalJsonDigest.sha256(canonicalMap).substring(0, 32);
        String payloadJson = toJson(canonicalMap);

        Optional<LearningPathEntity> existingOpt = pathRepo.findById(req.pathId());
        String baseRevision = "";
        LearningPathEntity entity;
        if (existingOpt.isPresent()) {
            entity = existingOpt.get();
            if (!entity.getUserId().equals(userId)) {
                throw new ConflictException("path_owned_by_another_user", "Path belongs to another user");
            }
            baseRevision = entity.getRevision();
            if (req.expectedRevision() != null && !req.expectedRevision().isBlank()
                    && !req.expectedRevision().equals(baseRevision)) {
                throw new ConflictException(
                        "stale_revision",
                        "Stale base revision: expected " + req.expectedRevision() + ", actual " + baseRevision
                );
            }
            entity.setTitle(title);
            entity.setGoal(goal);
            entity.setDomainId(domainId);
            entity.setSource(source);
            entity.setRevision(nextRevision);
            entity.setPayload(payloadJson);
            entity.setUpdatedAt(now);
        } else {
            if (req.expectedRevision() != null && !req.expectedRevision().isBlank()) {
                throw new ConflictException("stale_revision", "Expected revision provided for nonexistent path");
            }
            entity = new LearningPathEntity(
                    req.pathId(), userId, title, goal, domainId, source, nextRevision, payloadJson, now, now
            );
        }
        entity = pathRepo.saveAndFlush(entity);

        kpRepo.deleteByPathId(req.pathId());
        kpRepo.flush();
        List<KnowledgePointEntity> kpEntities = new ArrayList<>();
        for (int i = 0; i < specs.size(); i++) {
            ConceptDagValidator.KnowledgePointSpec s = specs.get(i);
            kpEntities.add(new KnowledgePointEntity(
                    req.pathId(),
                    s.kpId(),
                    s.title(),
                    s.kpType(),
                    i,
                    toJson(s.prerequisites()),
                    toJson(s)
            ));
        }
        kpRepo.saveAll(kpEntities);

        LearningPathRevisionEntity.Pk revPk = new LearningPathRevisionEntity.Pk(req.pathId(), nextRevision);
        if (!revisionRepo.existsById(revPk)) {
            revisionRepo.save(new LearningPathRevisionEntity(
                    req.pathId(),
                    nextRevision,
                    baseRevision,
                    nonNullOr(req.reason(), existingOpt.isPresent() ? "revise_path" : "initial_register"),
                    payloadJson,
                    now
            ));
        }

        return new PathResponse(
                entity.getPathId(), entity.getTitle(), entity.getGoal(), entity.getDomainId(),
                entity.getSource(), entity.getRevision(), entity.getVersion(),
                specs.stream().map(s -> new KpInput(s.kpId(), s.title(), s.kpType(), s.prerequisites())).toList(),
                entity.getUpdatedAt()
        );
    }

    @Transactional(readOnly = true)
    public List<PathResponse> listPaths(String userId) {
        return pathRepo.findByUserIdOrderByUpdatedAtDesc(userId).stream()
                .map(this::toPathResponse)
                .toList();
    }

    @Transactional(readOnly = true)
    public PathResponse getPath(String userId, String pathId) {
        LearningPathEntity entity = pathRepo.findByPathIdAndUserId(pathId, userId)
                .orElseThrow(() -> new NotFoundException("path_not_found", "Learning path not found: " + pathId));
        return toPathResponse(entity);
    }

    @Transactional
    public AttemptReceipt recordAttempt(String userId, RecordAttemptRequest req) {
        ConceptDagValidator.requireValidIdentifier(req.attemptId(), "attemptId");
        ConceptDagValidator.requireValidIdentifier(req.kpId(), "kpId");
        String interactionId = nonNull(req.interactionId());
        if (!interactionId.isBlank()) ConceptDagValidator.requireValidIdentifier(interactionId, "interactionId");

        String result = nonNull(req.result());
        String evidenceType = nonNull(req.evidenceType());
        validateResultAndEvidence(result, evidenceType);

        int hintCount = req.hintCount() != null ? req.hintCount() : 0;
        if (hintCount < 0 || hintCount > 100) {
            throw new DomainValidationException("invalid_hint_count", "hintCount must be in [0, 100]");
        }
        boolean answerRevealed = Boolean.TRUE.equals(req.answerRevealed());
        String assistance = nonNullOr(req.assistance(), "independent");
        if (!VALID_ASSISTANCE.contains(assistance)) {
            throw new DomainValidationException("invalid_attempt_result_or_assistance", "Invalid assistance: " + assistance);
        }
        if (answerRevealed) {
            assistance = "answer_revealed";
        } else if (hintCount > 0 && "independent".equals(assistance)) {
            assistance = "hinted";
        }

        double strength = req.strength() != null ? req.strength() : defaultStrengthFor(evidenceType);
        if (!Double.isFinite(strength) || strength < 0.0 || strength > 1.0) {
            throw new DomainValidationException("invalid_attempt_strength", "strength must be in [0, 1]");
        }

        if (tombstoneRepo.existsByAttemptIdAndUserId(req.attemptId(), userId)
                || (!interactionId.isBlank() && forgottenInteractionRepo.existsByInteractionIdAndUserId(interactionId, userId))) {
            MasteryEngine.MasteryResult current = recomputeMasteryForKp(userId, req.kpId(), nowSeconds(), null, 3, false);
            return new AttemptReceipt("ignored_forgotten", true, req.attemptId(), "", req.kpId(), result, assistance, current);
        }

        Map<String, Object> fingerprintDoc = buildFingerprintPayload(req, assistance, hintCount, answerRevealed, strength);
        String fingerprint = CanonicalJsonDigest.sha256(fingerprintDoc);

        Optional<LearningAttemptEntity> existingOpt = attemptRepo.findById(req.attemptId());
        if (existingOpt.isPresent()) {
            LearningAttemptEntity existing = existingOpt.get();
            if (!existing.getUserId().equals(userId) || !existing.getFingerprint().equals(fingerprint)) {
                throw new ConflictException(
                        "attempt_conflict_use_explicit_correction",
                        "Attempt ID already exists with different payload; use correction endpoint"
                );
            }
            MasteryEngine.MasteryResult current = getMastery(userId, req.kpId());
            return new AttemptReceipt("recorded", true, req.attemptId(), existing.getEvidenceId(), req.kpId(), result, assistance, current);
        }

        double recordedAt = (req.recordedAt() != null && req.recordedAt() > 0) ? req.recordedAt() : nowSeconds();
        String evidenceId = "ev:" + CanonicalJsonDigest.sha256(Map.of("userId", userId, "attemptId", req.attemptId())).substring(0, 32);
        boolean correct = "correct".equals(result);
        String turnId = nonNull(req.turnId());
        String errorCode = nonNull(req.errorCode());
        boolean reviewRecall = Boolean.TRUE.equals(req.reviewRecall());
        int rating = req.rating() != null ? Math.max(0, Math.min(4, req.rating())) : (correct ? 3 : 1);

        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("attempt_id", req.attemptId());
        meta.put("result", result);
        meta.put("assistance", assistance);
        meta.put("hint_count", hintCount);
        meta.put("answer_revealed", answerRevealed);
        meta.put("review_recall", reviewRecall);
        meta.put("item_family_id", nonNull(req.itemFamilyId()));
        meta.put("rating", rating);

        evidenceRepo.save(new LearningEvidenceEntity(
                evidenceId, userId, req.kpId(), evidenceType, strength, correct,
                interactionId, turnId, recordedAt, errorCode, toJson(meta)
        ));

        Map<String, Object> attemptPayload = new LinkedHashMap<>(fingerprintDoc);
        attemptPayload.put("recordedAt", recordedAt);
        attemptRepo.save(new LearningAttemptEntity(
                req.attemptId(), userId, req.kpId(), evidenceId, fingerprint, toJson(attemptPayload), recordedAt, ""
        ));

        boolean updateSchedule = reviewRecall || reviewRepo.findById(new ReviewItemEntity.Pk(userId, req.kpId())).isEmpty();
        int quality = deriveQuality(correct, assistance, rating, evidenceType);
        MasteryEngine.MasteryResult mastery = recomputeMasteryForKp(userId, req.kpId(), nowSeconds(), recordedAt, quality, updateSchedule);
        return new AttemptReceipt("recorded", false, req.attemptId(), evidenceId, req.kpId(), result, assistance, mastery);
    }

    @Transactional
    public AttemptReceipt correctAttempt(String userId, String targetAttemptId, CorrectAttemptRequest req) {
        ConceptDagValidator.requireValidIdentifier(req.correctionId(), "correctionId");
        LearningAttemptEntity original = attemptRepo.findByAttemptIdAndUserId(targetAttemptId, userId)
                .orElseThrow(() -> new NotFoundException("attempt_not_found", "Attempt not found: " + targetAttemptId));
        if (!original.getSupersededBy().isBlank()) {
            throw new ConflictException("correction_target_stale_or_mismatched", "Attempt was already superseded");
        }
        if (attemptRepo.existsById(req.correctionId())) {
            throw new ConflictException("correction_id_exists", "Correction ID already exists: " + req.correctionId());
        }

        JsonNode origNode = readTree(original.getPayload());
        String result = nonNull(req.result());
        String evidenceType = nonNullOr(req.evidenceType(), "correct".equals(result) ? "quiz_correct" : "quiz_incorrect");
        validateResultAndEvidence(result, evidenceType);
        double strength = req.strength() != null ? req.strength() : defaultStrengthFor(evidenceType);
        String assistance = origNode.path("assistance").asText("independent");
        int hintCount = origNode.path("hintCount").asInt(0);
        boolean answerRevealed = origNode.path("answerRevealed").asBoolean(false);
        String itemFamilyId = origNode.path("itemFamilyId").asText("");
        String interactionId = origNode.path("interactionId").asText("");
        String turnId = origNode.path("turnId").asText("");
        boolean reviewRecall = origNode.path("reviewRecall").asBoolean(false);
        int rating = "correct".equals(result) ? 3 : 1;

        original.setSupersededBy(req.correctionId());
        attemptRepo.save(original);

        String evidenceId = "ev:" + CanonicalJsonDigest.sha256(Map.of("userId", userId, "attemptId", req.correctionId())).substring(0, 32);
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("attempt_id", req.correctionId());
        meta.put("result", result);
        meta.put("assistance", assistance);
        meta.put("hint_count", hintCount);
        meta.put("answer_revealed", answerRevealed);
        meta.put("review_recall", reviewRecall);
        meta.put("item_family_id", itemFamilyId);
        meta.put("rating", rating);

        evidenceRepo.save(new LearningEvidenceEntity(
                evidenceId, userId, original.getKpId(), evidenceType, strength, "correct".equals(result),
                interactionId, turnId, original.getRecordedAt(), nonNull(req.errorCode()), toJson(meta)
        ));

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("attemptId", req.correctionId());
        payload.put("supersedesAttemptId", targetAttemptId);
        payload.put("kpId", original.getKpId());
        payload.put("result", result);
        payload.put("evidenceType", evidenceType);
        payload.put("assistance", assistance);
        payload.put("recordedAt", original.getRecordedAt());
        String fp = CanonicalJsonDigest.sha256(payload);
        attemptRepo.save(new LearningAttemptEntity(
                req.correctionId(), userId, original.getKpId(), evidenceId, fp, toJson(payload), original.getRecordedAt(), ""
        ));

        MasteryEngine.MasteryResult mastery = recomputeMasteryForKp(userId, original.getKpId(), nowSeconds(), null, 3, false);
        return new AttemptReceipt("corrected", false, req.correctionId(), evidenceId, original.getKpId(), result, assistance, mastery);
    }

    @Transactional
    public MasteryEngine.MasteryResult forgetAttempt(String userId, String attemptId) {
        double now = nowSeconds();
        Optional<LearningAttemptEntity> attemptOpt = attemptRepo.findByAttemptIdAndUserId(attemptId, userId);
        if (attemptOpt.isEmpty()) {
            throw new NotFoundException("attempt_not_found", "Attempt not found: " + attemptId);
        }
        LearningAttemptEntity attempt = attemptOpt.get();
        String kpId = attempt.getKpId();
        JsonNode payloadNode = readTree(attempt.getPayload());
        String interactionId = payloadNode.path("interactionId").asText("");

        if (!tombstoneRepo.existsById(attemptId)) {
            tombstoneRepo.save(new LearningForgetTombstoneEntity(attemptId, userId, now));
        }
        if (!interactionId.isBlank() && !forgottenInteractionRepo.existsById(interactionId)) {
            forgottenInteractionRepo.save(new LearningForgottenInteractionEntity(interactionId, userId, now));
        }
        if (!attempt.getEvidenceId().isBlank()) {
            evidenceRepo.deleteByEvidenceIdAndUserId(attempt.getEvidenceId(), userId);
        }
        attemptRepo.delete(attempt);
        attemptRepo.flush();
        evidenceRepo.flush();

        return recomputeMasteryForKp(userId, kpId, now, null, 1, false);
    }

    @Transactional
    public void recordAssistance(String userId, String scopeId, String kpId, String assistance, int hintDelta) {
        double now = nowSeconds();
        LearningAssistanceEntity entity = assistanceRepo.findById(scopeId).orElse(null);
        String nextAssistance = assistance;
        int nextHints = hintDelta;
        if (entity != null) {
            if ("answer_revealed".equals(entity.getAssistance()) || "answer_revealed".equals(assistance)) {
                nextAssistance = "answer_revealed";
            } else if ("hinted".equals(entity.getAssistance()) || "hinted".equals(assistance)) {
                nextAssistance = "hinted";
            }
            nextHints = entity.getHintCount() + hintDelta;
            entity.setAssistance(nextAssistance);
            entity.setHintCount(nextHints);
            entity.setUpdatedAt(now);
            assistanceRepo.save(entity);
        } else {
            assistanceRepo.save(new LearningAssistanceEntity(scopeId, userId, kpId, nextAssistance, nextHints, "[]", now));
        }
    }

    @Transactional(readOnly = true)
    public MasteryEngine.MasteryResult getMastery(String userId, String kpId) {
        return masteryRepo.findById(new MasteryRecordEntity.Pk(userId, kpId))
                .map(m -> new MasteryEngine.MasteryResult(
                        m.getKpId(), m.getMastery(), m.getConfidence(), m.getStage(),
                        m.getAttemptCount(), m.getCorrectCount(), m.getLastEvidenceAt(),
                        readStringList(m.getErrorCodesJson())
                ))
                .orElseGet(() -> new MasteryEngine.MasteryResult(kpId, 0.0, 0.0, "learning", 0, 0, 0.0, List.of()));
    }

    @Transactional(readOnly = true)
    public List<MasteryRecordEntity> listMastery(String userId) {
        return masteryRepo.findByUserIdOrderByUpdatedAtDesc(userId);
    }

    @Transactional(readOnly = true)
    public List<ReviewItemEntity> dueReviews(String userId) {
        return reviewRepo.findByUserIdAndDueAtLessThanEqualOrderByForgettingRiskDesc(userId, nowSeconds());
    }

    @Transactional(readOnly = true)
    public Map<String, Object> getLearnerContextSummary(String userId) {
        List<MasteryRecordEntity> records = masteryRepo.findByUserIdOrderByUpdatedAtDesc(userId);
        List<ReviewItemEntity> due = reviewRepo.findByUserIdAndDueAtLessThanEqualOrderByForgettingRiskDesc(userId, nowSeconds());
        List<Map<String, Object>> kps = records.stream().limit(10).map(r -> Map.<String, Object>of(
                "kpId", r.getKpId(),
                "mastery", r.getMastery(),
                "stage", r.getStage(),
                "attempts", r.getAttemptCount(),
                "correct", r.getCorrectCount()
        )).toList();
        return Map.of(
                "userId", userId,
                "trackedSkills", records.size(),
                "dueReviewCount", due.size(),
                "skills", kps
        );
    }

    private MasteryEngine.MasteryResult recomputeMasteryForKp(
            String userId, String kpId, double now, Double eventAt, int quality, boolean updateReview
    ) {
        List<LearningAttemptEntity> allAttempts = attemptRepo.findByUserIdAndKpIdOrderByRecordedAtAsc(userId, kpId);
        Set<String> supersededEvidenceIds = new HashSet<>();
        for (LearningAttemptEntity a : allAttempts) {
            if (a.getSupersededBy() != null && !a.getSupersededBy().isBlank() && !a.getEvidenceId().isBlank()) {
                supersededEvidenceIds.add(a.getEvidenceId());
            }
        }

        List<MasteryEngine.EvidenceObservation> effective = evidenceRepo
                .findByUserIdAndKpIdOrderByRecordedAtAsc(userId, kpId)
                .stream()
                .filter(e -> !supersededEvidenceIds.contains(e.getEvidenceId()))
                .map(e -> {
                    JsonNode meta = readTree(e.getMetadata());
                    return new MasteryEngine.EvidenceObservation(
                            e.getEvidenceId(),
                            e.getKpId(),
                            e.getEvidenceType(),
                            e.getStrength(),
                            e.isCorrect(),
                            e.getInteractionId(),
                            e.getTurnId(),
                            e.getRecordedAt(),
                            e.getErrorCode(),
                            meta.path("result").asText("correct"),
                            meta.path("assistance").asText("independent"),
                            meta.path("hint_count").asInt(0),
                            meta.path("answer_revealed").asBoolean(false),
                            meta.path("item_family_id").asText("")
                    );
                })
                .toList();

        MasteryEngine.MasteryResult result = MasteryEngine.computeMastery(kpId, effective, now);
        double retrievability = ReviewScheduler.computeRetrievability(result.lastEvidenceAt(), result.correctCount(), now);

        ReviewItemEntity existingReview = reviewRepo.findById(new ReviewItemEntity.Pk(userId, kpId)).orElse(null);
        double nextReviewAt = existingReview != null ? existingReview.getDueAt() : 0.0;
        if (updateReview && eventAt != null) {
            ReviewScheduler.ReviewSnapshot currentSnap = existingReview == null ? null : new ReviewScheduler.ReviewSnapshot(
                    kpId, existingReview.getDueAt(), existingReview.getForgettingRisk(),
                    existingReview.getIntervalDays(), existingReview.getEaseFactor()
            );
            ReviewScheduler.ReviewSnapshot next = ReviewScheduler.nextReviewItem(
                    kpId, result.lastEvidenceAt(), result.correctCount(), currentSnap, quality, eventAt
            );
            nextReviewAt = next.dueAt();
            reviewRepo.save(new ReviewItemEntity(
                    userId, kpId, next.dueAt(), next.forgettingRisk(), next.intervalDays(), next.easeFactor()
            ));
        }

        masteryRepo.save(new MasteryRecordEntity(
                userId, kpId, result.mastery(), result.confidence(), result.stage(),
                result.attemptCount(), result.correctCount(), result.lastEvidenceAt(),
                nextReviewAt, retrievability, toJson(result.errorCodes()), now
        ));
        return result;
    }

    private PathResponse toPathResponse(LearningPathEntity entity) {
        List<KpInput> kps = kpRepo.findByPathIdOrderByOrdinalAsc(entity.getPathId()).stream()
                .map(kp -> new KpInput(kp.getKpId(), kp.getTitle(), kp.getKpType(), readStringList(kp.getPrerequisitesJson())))
                .toList();
        return new PathResponse(
                entity.getPathId(), entity.getTitle(), entity.getGoal(), entity.getDomainId(),
                entity.getSource(), entity.getRevision(), entity.getVersion(), kps, entity.getUpdatedAt()
        );
    }

    private void validateResultAndEvidence(String result, String evidenceType) {
        if (!VALID_RESULTS.contains(result)) {
            throw new DomainValidationException("invalid_attempt_result_or_assistance", "Invalid result: " + result);
        }
        if ("correct".equals(result) && NEGATIVE_EVIDENCE.contains(evidenceType)) {
            throw new DomainValidationException("attempt_result_evidence_mismatch", "Correct result cannot have negative evidenceType");
        }
        if ("incorrect".equals(result) && POSITIVE_EVIDENCE.contains(evidenceType)) {
            throw new DomainValidationException("attempt_result_evidence_mismatch", "Incorrect result cannot have positive evidenceType");
        }
    }

    private static int deriveQuality(boolean correct, String assistance, int rating, String evidenceType) {
        if ("misconception".equals(evidenceType)) return 0;
        if (!correct) return 1;
        if (!"independent".equals(assistance)) return 2;
        return Math.max(3, Math.min(4, rating));
    }

    private static double defaultStrengthFor(String evidenceType) {
        return switch (evidenceType) {
            case "quiz_correct", "feynman_pass" -> 1.0;
            case "application" -> 0.85;
            case "paraphrase" -> 0.70;
            case "self_report" -> 0.20;
            case "exposure" -> 0.10;
            default -> 0.0;
        };
    }

    private Map<String, Object> buildFingerprintPayload(
            RecordAttemptRequest req, String assistance, int hintCount, boolean answerRevealed, double strength
    ) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("attemptId", req.attemptId());
        map.put("kpId", req.kpId());
        map.put("result", req.result());
        map.put("evidenceType", req.evidenceType());
        map.put("strength", strength);
        map.put("interactionId", nonNull(req.interactionId()));
        map.put("turnId", nonNull(req.turnId()));
        map.put("pathId", nonNull(req.pathId()));
        map.put("itemId", nonNull(req.itemId()));
        map.put("itemFamilyId", nonNull(req.itemFamilyId()));
        map.put("activityId", nonNull(req.activityId()));
        map.put("assistance", assistance);
        map.put("hintCount", hintCount);
        map.put("answerRevealed", answerRevealed);
        map.put("reviewRecall", Boolean.TRUE.equals(req.reviewRecall()));
        map.put("rating", req.rating() != null ? req.rating() : 0);
        map.put("errorCode", nonNull(req.errorCode()));
        return map;
    }

    private JsonNode readTree(String json) {
        try {
            return objectMapper.readTree(json == null || json.isBlank() ? "{}" : json);
        } catch (JacksonException e) {
            return objectMapper.createObjectNode();
        }
    }

    private List<String> readStringList(String json) {
        try {
            JsonNode node = objectMapper.readTree(json == null || json.isBlank() ? "[]" : json);
            List<String> out = new ArrayList<>();
            if (node.isArray()) {
                for (JsonNode n : node) {
                    out.add(n.asText());
                }
            }
            return out;
        } catch (JacksonException e) {
            return List.of();
        }
    }

    private String toJson(Object obj) {
        return objectMapper.writeValueAsString(obj);
    }

    private static double nowSeconds() {
        return Instant.now().toEpochMilli() / 1000.0;
    }

    private static String nonNull(String v) {
        return v == null ? "" : v.trim();
    }

    private static String nonNullOr(String v, String fallback) {
        return (v == null || v.isBlank()) ? fallback : v.trim();
    }
}
