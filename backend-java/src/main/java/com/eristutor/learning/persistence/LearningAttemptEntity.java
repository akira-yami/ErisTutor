package com.eristutor.learning.persistence;

import jakarta.persistence.*;

@Entity
@Table(name = "learning_attempts")
public class LearningAttemptEntity {

    @Id
    @Column(name = "attempt_id", length = 120)
    private String attemptId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "kp_id", nullable = false, length = 120)
    private String kpId;

    @Column(name = "evidence_id", nullable = false, length = 120)
    private String evidenceId;

    @Column(nullable = false, length = 64)
    private String fingerprint;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String payload;

    @Column(name = "recorded_at", nullable = false)
    private double recordedAt;

    @Column(name = "superseded_by", nullable = false, length = 120)
    private String supersededBy;

    protected LearningAttemptEntity() {}

    public LearningAttemptEntity(String attemptId, String userId, String kpId, String evidenceId, String fingerprint,
                                 String payload, double recordedAt, String supersededBy) {
        this.attemptId = attemptId;
        this.userId = userId;
        this.kpId = kpId;
        this.evidenceId = evidenceId;
        this.fingerprint = fingerprint;
        this.payload = payload;
        this.recordedAt = recordedAt;
        this.supersededBy = supersededBy;
    }

    public String getAttemptId() { return attemptId; }
    public String getUserId() { return userId; }
    public String getKpId() { return kpId; }
    public String getEvidenceId() { return evidenceId; }
    public String getFingerprint() { return fingerprint; }
    public String getPayload() { return payload; }
    public double getRecordedAt() { return recordedAt; }
    public String getSupersededBy() { return supersededBy; }
    public void setSupersededBy(String supersededBy) { this.supersededBy = supersededBy; }
}
