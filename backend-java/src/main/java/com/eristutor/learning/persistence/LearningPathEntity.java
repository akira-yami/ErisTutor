package com.eristutor.learning.persistence;

import jakarta.persistence.*;

@Entity
@Table(name = "learning_paths")
public class LearningPathEntity {

    @Id
    @Column(name = "path_id", length = 120)
    private String pathId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(nullable = false, length = 240)
    private String title;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String goal;

    @Column(name = "domain_id", nullable = false, length = 120)
    private String domainId;

    @Column(nullable = false, length = 64)
    private String source;

    @Column(nullable = false, length = 64)
    private String revision;

    @Version
    @Column(nullable = false)
    private long version;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String payload;

    @Column(name = "created_at", nullable = false)
    private double createdAt;

    @Column(name = "updated_at", nullable = false)
    private double updatedAt;

    protected LearningPathEntity() {}

    public LearningPathEntity(String pathId, String userId, String title, String goal, String domainId,
                              String source, String revision, String payload, double createdAt, double updatedAt) {
        this.pathId = pathId;
        this.userId = userId;
        this.title = title;
        this.goal = goal;
        this.domainId = domainId;
        this.source = source;
        this.revision = revision;
        this.payload = payload;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public String getPathId() { return pathId; }
    public String getUserId() { return userId; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getGoal() { return goal; }
    public void setGoal(String goal) { this.goal = goal; }
    public String getDomainId() { return domainId; }
    public void setDomainId(String domainId) { this.domainId = domainId; }
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    public String getRevision() { return revision; }
    public void setRevision(String revision) { this.revision = revision; }
    public long getVersion() { return version; }
    public String getPayload() { return payload; }
    public void setPayload(String payload) { this.payload = payload; }
    public double getCreatedAt() { return createdAt; }
    public double getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(double updatedAt) { this.updatedAt = updatedAt; }
}
