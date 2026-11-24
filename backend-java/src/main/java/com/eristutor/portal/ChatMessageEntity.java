package com.eristutor.portal;

import jakarta.persistence.*;

@Entity
@Table(name = "chat_messages")
public class ChatMessageEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "session_id", nullable = false, length = 120)
    private String sessionId;

    @Column(name = "user_id", nullable = false, length = 64)
    private String userId;

    @Column(nullable = false, length = 32)
    private String role;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "turn_id", nullable = false, length = 64)
    private String turnId;

    @Column(name = "created_at", nullable = false)
    private double createdAt;

    protected ChatMessageEntity() {}

    public ChatMessageEntity(String sessionId, String userId, String role, String content, String turnId, double createdAt) {
        this.sessionId = sessionId;
        this.userId = userId;
        this.role = role;
        this.content = content;
        this.turnId = turnId;
        this.createdAt = createdAt;
    }

    public Long getId() { return id; }
    public String getSessionId() { return sessionId; }
    public String getUserId() { return userId; }
    public String getRole() { return role; }
    public String getContent() { return content; }
    public String getTurnId() { return turnId; }
    public double getCreatedAt() { return createdAt; }
}
