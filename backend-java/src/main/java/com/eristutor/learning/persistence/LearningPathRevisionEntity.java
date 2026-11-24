package com.eristutor.learning.persistence;

import jakarta.persistence.*;
import java.io.Serializable;
import java.util.Objects;

@Entity
@Table(name = "learning_path_revisions")
@IdClass(LearningPathRevisionEntity.Pk.class)
public class LearningPathRevisionEntity {

    @Id
    @Column(name = "path_id", length = 120)
    private String pathId;

    @Id
    @Column(length = 64)
    private String revision;

    @Column(name = "base_revision", nullable = false, length = 64)
    private String baseRevision;

    @Column(nullable = false, length = 240)
    private String reason;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String payload;

    @Column(name = "created_at", nullable = false)
    private double createdAt;

    protected LearningPathRevisionEntity() {}

    public LearningPathRevisionEntity(String pathId, String revision, String baseRevision, String reason, String payload, double createdAt) {
        this.pathId = pathId;
        this.revision = revision;
        this.baseRevision = baseRevision;
        this.reason = reason;
        this.payload = payload;
        this.createdAt = createdAt;
    }

    public String getPathId() { return pathId; }
    public String getRevision() { return revision; }
    public String getBaseRevision() { return baseRevision; }
    public String getReason() { return reason; }
    public String getPayload() { return payload; }
    public double getCreatedAt() { return createdAt; }

    public static class Pk implements Serializable {
        private String pathId;
        private String revision;
        public Pk() {}
        public Pk(String pathId, String revision) { this.pathId = pathId; this.revision = revision; }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Pk pk)) return false;
            return Objects.equals(pathId, pk.pathId) && Objects.equals(revision, pk.revision);
        }

        @Override
        public int hashCode() { return Objects.hash(pathId, revision); }
    }
}
