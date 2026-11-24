package com.eristutor.learning.persistence;

import jakarta.persistence.*;
import java.io.Serializable;
import java.util.Objects;

@Entity
@Table(name = "review_items")
@IdClass(ReviewItemEntity.Pk.class)
public class ReviewItemEntity {

    @Id
    @Column(name = "user_id", length = 64)
    private String userId;

    @Id
    @Column(name = "kp_id", length = 120)
    private String kpId;

    @Column(name = "due_at", nullable = false)
    private double dueAt;

    @Column(name = "forgetting_risk", nullable = false)
    private double forgettingRisk;

    @Column(name = "interval_days", nullable = false)
    private double intervalDays;

    @Column(name = "ease_factor", nullable = false)
    private double easeFactor;

    protected ReviewItemEntity() {}

    public ReviewItemEntity(String userId, String kpId, double dueAt, double forgettingRisk,
                            double intervalDays, double easeFactor) {
        this.userId = userId;
        this.kpId = kpId;
        this.dueAt = dueAt;
        this.forgettingRisk = forgettingRisk;
        this.intervalDays = intervalDays;
        this.easeFactor = easeFactor;
    }

    public String getUserId() { return userId; }
    public String getKpId() { return kpId; }
    public double getDueAt() { return dueAt; }
    public double getForgettingRisk() { return forgettingRisk; }
    public double getIntervalDays() { return intervalDays; }
    public double getEaseFactor() { return easeFactor; }

    public static class Pk implements Serializable {
        private String userId;
        private String kpId;
        public Pk() {}
        public Pk(String userId, String kpId) { this.userId = userId; this.kpId = kpId; }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Pk pk)) return false;
            return Objects.equals(userId, pk.userId) && Objects.equals(kpId, pk.kpId);
        }

        @Override
        public int hashCode() { return Objects.hash(userId, kpId); }
    }
}
