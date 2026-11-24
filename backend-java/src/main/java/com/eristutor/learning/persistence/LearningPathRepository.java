package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface LearningPathRepository extends JpaRepository<LearningPathEntity, String> {
    List<LearningPathEntity> findByUserIdOrderByUpdatedAtDesc(String userId);
    Optional<LearningPathEntity> findByPathIdAndUserId(String pathId, String userId);
}
