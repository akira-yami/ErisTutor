package com.eristutor.learning.persistence;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "learning_forgotten_interactions")
public class LearningForgottenInteractionEntity {

    @Id
    @Column(name = "interaction_id", length = 120)
    private String interactionId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "forgotten_at", nullable = false)
    private double forgottenAt;

    protected LearningForgottenInteractionEntity() {}

    public LearningForgottenInteractionEntity(String interactionId, String userId, double forgottenAt) {
        this.interactionId = interactionId;
        this.userId = userId;
        this.forgottenAt = forgottenAt;
    }

    public String getInteractionId() { return interactionId; }
    public String getUserId() { return userId; }
    public double getForgottenAt() { return forgottenAt; }
}
