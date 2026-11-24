package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface LearningPathRevisionRepository extends JpaRepository<LearningPathRevisionEntity, LearningPathRevisionEntity.Pk> {
    List<LearningPathRevisionEntity> findByPathIdOrderByCreatedAtAsc(String pathId);
}
