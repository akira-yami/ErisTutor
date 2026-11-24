package com.eristutor.judge;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface JudgeSubmissionRepository extends JpaRepository<JudgeSubmissionEntity, String> {
    Optional<JudgeSubmissionEntity> findByUserIdAndRequestId(String userId, String requestId);
    Optional<JudgeSubmissionEntity> findByJobIdAndUserId(String jobId, String userId);
}
