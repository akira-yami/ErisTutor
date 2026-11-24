package com.eristutor.learning.persistence;

import jakarta.persistence.*;
import java.io.Serializable;
import java.util.Objects;

@Entity
@Table(name = "mastery_records")
@IdClass(MasteryRecordEntity.Pk.class)
public class MasteryRecordEntity {

    @Id
    @Column(name = "user_id", length = 64)
    private String userId;

    @Id
    @Column(name = "kp_id", length = 120)
    private String kpId;

    @Column(nullable = false)
    private double mastery;

    @Column(nullable = false)
    private double confidence;

    @Column(nullable = false, length = 32)
    private String stage;

    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;

    @Column(name = "correct_count", nullable = false)
    private int correctCount;

    @Column(name = "last_evidence_at", nullable = false)
    private double lastEvidenceAt;

    @Column(name = "next_review_at", nullable = false)
    private double nextReviewAt;

    @Column(nullable = false)
    private double retrievability;

    @Column(name = "error_codes_json", nullable = false, columnDefinition = "TEXT")
    private String errorCodesJson;

    @Column(name = "updated_at", nullable = false)
    private double updatedAt;

    protected MasteryRecordEntity() {}

    public MasteryRecordEntity(String userId, String kpId, double mastery, double confidence, String stage,
                               int attemptCount, int correctCount, double lastEvidenceAt, double nextReviewAt,
                               double retrievability, String errorCodesJson, double updatedAt) {
        this.userId = userId;
        this.kpId = kpId;
        this.mastery = mastery;
        this.confidence = confidence;
        this.stage = stage;
        this.attemptCount = attemptCount;
        this.correctCount = correctCount;
        this.lastEvidenceAt = lastEvidenceAt;
        this.nextReviewAt = nextReviewAt;
        this.retrievability = retrievability;
        this.errorCodesJson = errorCodesJson;
        this.updatedAt = updatedAt;
    }

    public String getUserId() { return userId; }
    public String getKpId() { return kpId; }
    public double getMastery() { return mastery; }
    public double getConfidence() { return confidence; }
    public String getStage() { return stage; }
    public int getAttemptCount() { return attemptCount; }
    public int getCorrectCount() { return correctCount; }
    public double getLastEvidenceAt() { return lastEvidenceAt; }
    public double getNextReviewAt() { return nextReviewAt; }
    public double getRetrievability() { return retrievability; }
    public String getErrorCodesJson() { return errorCodesJson; }
    public double getUpdatedAt() { return updatedAt; }

    public static class Pk implements Serializable {
        private String userId;
        private String kpId;
        public Pk() {}
        public Pk(String userId, String kpId) { this.userId = userId; this.kpId = kpId; }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Pk pk)) return false;
            return Objects.equals(userId, pk.userId) && Objects.equals(kpId, pk.kpId);
        }

        @Override
        public int hashCode() { return Objects.hash(userId, kpId); }
    }
}
