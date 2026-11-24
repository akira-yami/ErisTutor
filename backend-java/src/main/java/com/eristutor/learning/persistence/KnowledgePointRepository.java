package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface KnowledgePointRepository extends JpaRepository<KnowledgePointEntity, KnowledgePointEntity.Pk> {
    List<KnowledgePointEntity> findByPathIdOrderByOrdinalAsc(String pathId);
    void deleteByPathId(String pathId);
}
