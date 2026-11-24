package com.eristutor.learning.persistence;

import jakarta.persistence.*;
import java.io.Serializable;
import java.util.Objects;

@Entity
@Table(name = "knowledge_points")
@IdClass(KnowledgePointEntity.Pk.class)
public class KnowledgePointEntity {

    @Id
    @Column(name = "path_id", length = 120)
    private String pathId;

    @Id
    @Column(name = "kp_id", length = 120)
    private String kpId;

    @Column(nullable = false, length = 240)
    private String title;

    @Column(name = "kp_type", nullable = false, length = 32)
    private String kpType;

    @Column(nullable = false)
    private int ordinal;

    @Column(name = "prerequisites_json", nullable = false, columnDefinition = "TEXT")
    private String prerequisitesJson;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String payload;

    protected KnowledgePointEntity() {}

    public KnowledgePointEntity(String pathId, String kpId, String title, String kpType, int ordinal,
                                String prerequisitesJson, String payload) {
        this.pathId = pathId;
        this.kpId = kpId;
        this.title = title;
        this.kpType = kpType;
        this.ordinal = ordinal;
        this.prerequisitesJson = prerequisitesJson;
        this.payload = payload;
    }

    public String getPathId() { return pathId; }
    public String getKpId() { return kpId; }
    public String getTitle() { return title; }
    public String getKpType() { return kpType; }
    public int getOrdinal() { return ordinal; }
    public String getPrerequisitesJson() { return prerequisitesJson; }
    public String getPayload() { return payload; }

    public static class Pk implements Serializable {
        private String pathId;
        private String kpId;

        public Pk() {}
        public Pk(String pathId, String kpId) { this.pathId = pathId; this.kpId = kpId; }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Pk pk)) return false;
            return Objects.equals(pathId, pk.pathId) && Objects.equals(kpId, pk.kpId);
        }

        @Override
        public int hashCode() {
            return Objects.hash(pathId, kpId);
        }
    }
}
