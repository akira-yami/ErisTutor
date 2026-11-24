package com.eristutor.security;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "refresh_tokens")
public class RefreshTokenEntity {

    @Id
    @Column(name = "token_hash", length = 64)
    private String tokenHash;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(name = "expires_at", nullable = false)
    private double expiresAt;

    @Column(nullable = false)
    private boolean revoked;

    @Column(name = "created_at", nullable = false)
    private double createdAt;

    protected RefreshTokenEntity() {}

    public RefreshTokenEntity(String tokenHash, String userId, double expiresAt, boolean revoked, double createdAt) {
        this.tokenHash = tokenHash;
        this.userId = userId;
        this.expiresAt = expiresAt;
        this.revoked = revoked;
        this.createdAt = createdAt;
    }

    public String getTokenHash() { return tokenHash; }
    public String getUserId() { return userId; }
    public double getExpiresAt() { return expiresAt; }
    public boolean isRevoked() { return revoked; }
    public void setRevoked(boolean revoked) { this.revoked = revoked; }
    public double getCreatedAt() { return createdAt; }
}
