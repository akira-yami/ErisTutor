package com.eristutor.learning.persistence;

import jakarta.persistence.*;

@Entity
@Table(name = "learning_evidence")
public class LearningEvidenceEntity {

    @Id
    @Column(name = "evidence_id", length = 120)
    private String evidenceId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "kp_id", nullable = false, length = 120)
    private String kpId;

    @Column(name = "evidence_type", nullable = false, length = 40)
    private String evidenceType;

    @Column(nullable = false)
    private double strength;

    @Column(nullable = false)
    private boolean correct;

    @Column(name = "interaction_id", nullable = false, length = 120)
    private String interactionId;

    @Column(name = "turn_id", nullable = false, length = 120)
    private String turnId;

    @Column(name = "recorded_at", nullable = false)
    private double recordedAt;

    @Column(name = "error_code", nullable = false, length = 120)
    private String errorCode;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String metadata;

    protected LearningEvidenceEntity() {}

    public LearningEvidenceEntity(String evidenceId, String userId, String kpId, String evidenceType, double strength,
                                  boolean correct, String interactionId, String turnId, double recordedAt,
                                  String errorCode, String metadata) {
        this.evidenceId = evidenceId;
        this.userId = userId;
        this.kpId = kpId;
        this.evidenceType = evidenceType;
        this.strength = strength;
        this.correct = correct;
        this.interactionId = interactionId;
        this.turnId = turnId;
        this.recordedAt = recordedAt;
        this.errorCode = errorCode;
        this.metadata = metadata;
    }

    public String getEvidenceId() { return evidenceId; }
    public String getUserId() { return userId; }
    public String getKpId() { return kpId; }
    public String getEvidenceType() { return evidenceType; }
    public double getStrength() { return strength; }
    public boolean isCorrect() { return correct; }
    public String getInteractionId() { return interactionId; }
    public String getTurnId() { return turnId; }
    public double getRecordedAt() { return recordedAt; }
    public String getErrorCode() { return errorCode; }
    public String getMetadata() { return metadata; }
}
