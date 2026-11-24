package com.eristutor.judge;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "judge_submissions")
public class JudgeSubmissionEntity {

    @Id
    @Column(name = "job_id", length = 80)
    private String jobId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "request_id", nullable = false, length = 120)
    private String requestId;

    @Column(name = "pack_id", nullable = false, length = 120)
    private String packId;

    @Column(name = "node_id", nullable = false, length = 120)
    private String nodeId;

    @Column(name = "exercise_id", nullable = false, length = 120)
    private String exerciseId;

    @Column(nullable = false, length = 32)
    private String status;

    @Column(nullable = false, length = 32)
    private String result;

    @Column(nullable = false, length = 120)
    private String reason;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String diagnostic;

    @Column(name = "duration_ms", nullable = false)
    private long durationMs;

    @Column(name = "exit_code", nullable = false)
    private int exitCode;

    @Column(name = "tests_json", nullable = false, columnDefinition = "TEXT")
    private String testsJson;

    @Column(name = "attempt_id", nullable = false, length = 120)
    private String attemptId;

    @Column(name = "created_at", nullable = false)
    private double createdAt;

    @Column(name = "completed_at", nullable = false)
    private double completedAt;

    protected JudgeSubmissionEntity() {}

    public JudgeSubmissionEntity(String jobId, String userId, String requestId, String packId, String nodeId,
                                 String exerciseId, String status, double createdAt) {
        this.jobId = jobId;
        this.userId = userId;
        this.requestId = requestId;
        this.packId = packId;
        this.nodeId = nodeId;
        this.exerciseId = exerciseId;
        this.status = status;
        this.result = "";
        this.reason = "";
        this.diagnostic = "";
        this.durationMs = 0;
        this.exitCode = 0;
        this.testsJson = "[]";
        this.attemptId = "";
        this.createdAt = createdAt;
        this.completedAt = 0;
    }

    public String getJobId() { return jobId; }
    public String getUserId() { return userId; }
    public String getRequestId() { return requestId; }
    public String getPackId() { return packId; }
    public String getNodeId() { return nodeId; }
    public String getExerciseId() { return exerciseId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getResult() { return result; }
    public void setResult(String result) { this.result = result; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public String getDiagnostic() { return diagnostic; }
    public void setDiagnostic(String diagnostic) { this.diagnostic = diagnostic; }
    public long getDurationMs() { return durationMs; }
    public void setDurationMs(long durationMs) { this.durationMs = durationMs; }
    public int getExitCode() { return exitCode; }
    public void setExitCode(int exitCode) { this.exitCode = exitCode; }
    public String getTestsJson() { return testsJson; }
    public void setTestsJson(String testsJson) { this.testsJson = testsJson; }
    public String getAttemptId() { return attemptId; }
    public void setAttemptId(String attemptId) { this.attemptId = attemptId; }
    public double getCreatedAt() { return createdAt; }
    public double getCompletedAt() { return completedAt; }
    public void setCompletedAt(double completedAt) { this.completedAt = completedAt; }
}
