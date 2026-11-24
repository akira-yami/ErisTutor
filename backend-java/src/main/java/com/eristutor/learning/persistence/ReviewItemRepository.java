package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ReviewItemRepository extends JpaRepository<ReviewItemEntity, ReviewItemEntity.Pk> {
    List<ReviewItemEntity> findByUserIdAndDueAtLessThanEqualOrderByForgettingRiskDesc(String userId, double nowSeconds);
    List<ReviewItemEntity> findByUserIdOrderByDueAtAsc(String userId);
}
