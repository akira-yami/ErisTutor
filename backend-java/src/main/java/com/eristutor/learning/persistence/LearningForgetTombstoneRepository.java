package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

public interface LearningForgetTombstoneRepository extends JpaRepository<LearningForgetTombstoneEntity, String> {
    boolean existsByAttemptIdAndUserId(String attemptId, String userId);
}
