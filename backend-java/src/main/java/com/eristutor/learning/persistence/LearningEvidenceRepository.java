package com.eristutor.learning.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface LearningEvidenceRepository extends JpaRepository<LearningEvidenceEntity, String> {
    List<LearningEvidenceEntity> findByUserIdAndKpIdOrderByRecordedAtAsc(String userId, String kpId);
    void deleteByEvidenceIdAndUserId(String evidenceId, String userId);
}
