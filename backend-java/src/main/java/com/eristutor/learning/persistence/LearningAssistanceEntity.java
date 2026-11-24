package com.eristutor.learning.persistence;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "learning_assistance")
public class LearningAssistanceEntity {

    @Id
    @Column(name = "scope_id", length = 160)
    private String scopeId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "kp_id", nullable = false, length = 120)
    private String kpId;

    @Column(nullable = false, length = 40)
    private String assistance;

    @Column(name = "hint_count", nullable = false)
    private int hintCount;

    @Column(name = "events_json", nullable = false, columnDefinition = "TEXT")
    private String eventsJson;

    @Column(name = "updated_at", nullable = false)
    private double updatedAt;

    protected LearningAssistanceEntity() {}

    public LearningAssistanceEntity(String scopeId, String userId, String kpId, String assistance,
                                    int hintCount, String eventsJson, double updatedAt) {
        this.scopeId = scopeId;
        this.userId = userId;
        this.kpId = kpId;
        this.assistance = assistance;
        this.hintCount = hintCount;
        this.eventsJson = eventsJson;
        this.updatedAt = updatedAt;
    }

    public String getScopeId() { return scopeId; }
    public String getUserId() { return userId; }
    public String getKpId() { return kpId; }
    public String getAssistance() { return assistance; }
    public void setAssistance(String assistance) { this.assistance = assistance; }
    public int getHintCount() { return hintCount; }
    public void setHintCount(int hintCount) { this.hintCount = hintCount; }
    public String getEventsJson() { return eventsJson; }
    public void setEventsJson(String eventsJson) { this.eventsJson = eventsJson; }
    public double getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(double updatedAt) { this.updatedAt = updatedAt; }
}
