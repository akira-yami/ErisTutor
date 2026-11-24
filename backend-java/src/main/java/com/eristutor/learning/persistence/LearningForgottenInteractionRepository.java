package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

public interface LearningForgottenInteractionRepository extends JpaRepository<LearningForgottenInteractionEntity, String> {
    boolean existsByInteractionIdAndUserId(String interactionId, String userId);
}
