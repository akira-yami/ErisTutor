package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MasteryRecordRepository extends JpaRepository<MasteryRecordEntity, MasteryRecordEntity.Pk> {
    List<MasteryRecordEntity> findByUserIdOrderByUpdatedAtDesc(String userId);
}
