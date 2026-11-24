package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface LearningAttemptRepository extends JpaRepository<LearningAttemptEntity, String> {
    Optional<LearningAttemptEntity> findByAttemptIdAndUserId(String attemptId, String userId);
    List<LearningAttemptEntity> findByUserIdAndKpIdOrderByRecordedAtAsc(String userId, String kpId);
}
