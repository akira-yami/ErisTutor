package com.eristutor.learning.persistence;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "learning_forget_tombstones")
public class LearningForgetTombstoneEntity {

    @Id
    @Column(name = "attempt_id", length = 120)
    private String attemptId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "forgotten_at", nullable = false)
    private double forgottenAt;

    protected LearningForgetTombstoneEntity() {}

    public LearningForgetTombstoneEntity(String attemptId, String userId, double forgottenAt) {
        this.attemptId = attemptId;
        this.userId = userId;
        this.forgottenAt = forgottenAt;
    }

    public String getAttemptId() { return attemptId; }
    public String getUserId() { return userId; }
    public double getForgottenAt() { return forgottenAt; }
}
