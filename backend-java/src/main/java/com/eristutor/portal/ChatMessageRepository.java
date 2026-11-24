package com.eristutor.portal;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ChatMessageRepository extends JpaRepository<ChatMessageEntity, Long> {
    List<ChatMessageEntity> findBySessionIdOrderByIdAsc(String sessionId);
    void deleteBySessionId(String sessionId);
    long countBySessionId(String sessionId);
}
