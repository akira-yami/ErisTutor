package com.eristutor.portal;

import com.eristutor.ai.LlmGatewayService;
import com.eristutor.ai.RateLimiter;
import com.eristutor.common.DomainValidationException;
import com.eristutor.common.RateLimitExceededException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class ChatPortalService {

    private static final DateTimeFormatter DATE_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("HH:mm");

    private static final String WELCOME_MESSAGE = """
            > 🧠 **Nội tâm của Eris:**
            > *Cổng Java 27 Cloud Studio đã mở. Để xem hôm nay học sinh nào ghé thăm hay là ban giám khảo kỹ thuật đang kiểm tra kiến trúc...*

            Chào bạn, tôi là **Eris**. Bạn đang kết nối với phiên bản **Eris Cloud Studio (Java 27 + Spring Boot 4)**.

            Hôm nay muốn bàn về bài toán hóc búa nào, hay định luyện Java Dependency Injection trên Canvas?""";

    private static final class SessionRuntime {
        volatile String status = "Sẵn sàng";
        volatile String phase = "idle";
        final AtomicLong revision = new AtomicLong(System.currentTimeMillis());
        final AtomicInteger turnCount = new AtomicInteger(0);
        final CopyOnWriteArrayList<SseEmitter> subscribers = new CopyOnWriteArrayList<>();
    }

    private final ChatMessageRepository messageRepo;
    private final LlmGatewayService llmGateway;
    private final RateLimiter rateLimiter;
    private final ObjectMapper objectMapper;
    private final ConcurrentMap<String, SessionRuntime> sessions = new ConcurrentHashMap<>();

    public ChatPortalService(
            ChatMessageRepository messageRepo,
            LlmGatewayService llmGateway,
            RateLimiter rateLimiter,
            ObjectMapper objectMapper
    ) {
        this.messageRepo = messageRepo;
        this.llmGateway = llmGateway;
        this.rateLimiter = rateLimiter;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public Map<String, Object> getSnapshot(String sessionId, String userId) {
        ensureSeeded(sessionId, userId);
        return buildSnapshot(sessionId);
    }

    public Map<String, Object> sendMessage(String sessionId, String userId, String rawMessage) {
        String text = rawMessage == null ? "" : rawMessage.trim();
        if (text.isEmpty()) {
            throw new DomainValidationException("empty_message", "Message must not be empty");
        }
        RateLimiter.Decision decision = rateLimiter.tryConsume(userId + ":" + sessionId);
        if (!decision.allowed()) {
            throw new RateLimitExceededException(decision.retryAfterSeconds());
        }

        ensureSeeded(sessionId, userId);
        SessionRuntime rt = runtimeFor(sessionId);

        double now = Instant.now().toEpochMilli() / 1000.0;
        messageRepo.save(new ChatMessageEntity(sessionId, userId, "user", text, "", now));
        rt.status = "Đang suy nghĩ...";
        rt.phase = "thinking";
        rt.revision.set(System.currentTimeMillis());
        notifySubscribers(sessionId);

        List<ChatMessageEntity> historyRows = messageRepo.findBySessionIdOrderByIdAsc(sessionId);
        int fromIdx = Math.max(0, historyRows.size() - 7);
        List<LlmGatewayService.ChatTurn> recent = historyRows.subList(fromIdx, Math.max(fromIdx, historyRows.size() - 1))
                .stream()
                .map(m -> new LlmGatewayService.ChatTurn(m.getRole(), m.getContent()))
                .toList();

        String replyText;
        String provider;
        try {
            LlmGatewayService.LlmReply reply = llmGateway.generateReply(userId, text, recent);
            replyText = reply.text();
            provider = reply.provider();
        } catch (Exception ex) {
            replyText = "> 🧠 **Nội tâm:** *Đứt kết nối mạng...*\n\nCó vẻ mạng hơi chập chờn rồi: " + ex.getMessage();
            provider = "error-fallback";
        }

        int turn = rt.turnCount.incrementAndGet();
        double replyAt = Instant.now().toEpochMilli() / 1000.0;
        messageRepo.save(new ChatMessageEntity(sessionId, userId, "assistant", replyText, "turn_" + turn, replyAt));

        rt.status = "Sẵn sàng";
        rt.phase = "idle";
        rt.revision.set(System.currentTimeMillis());
        notifySubscribers(sessionId);

        return Map.of("ok", true, "provider", provider, "turnId", "turn_" + turn);
    }

    @Transactional
    public Map<String, Object> deleteHistory(String sessionId) {
        messageRepo.deleteBySessionId(sessionId);
        SessionRuntime rt = runtimeFor(sessionId);
        rt.turnCount.set(0);
        rt.revision.set(System.currentTimeMillis());
        notifySubscribers(sessionId);
        return Map.of("ok", true);
    }

    public SseEmitter subscribe(String sessionId, String userId) {
        ensureSeeded(sessionId, userId);
        SessionRuntime rt = runtimeFor(sessionId);
        SseEmitter emitter = new SseEmitter(0L);
        rt.subscribers.add(emitter);
        emitter.onCompletion(() -> rt.subscribers.remove(emitter));
        emitter.onTimeout(() -> rt.subscribers.remove(emitter));
        emitter.onError(e -> rt.subscribers.remove(emitter));

        try {
            String initialJson = objectMapper.writeValueAsString(buildSnapshot(sessionId));
            emitter.send(SseEmitter.event().name("state").data(initialJson));
        } catch (IOException e) {
            rt.subscribers.remove(emitter);
        }
        return emitter;
    }

    private void ensureSeeded(String sessionId, String userId) {
        if (messageRepo.countBySessionId(sessionId) == 0) {
            double now = Instant.now().toEpochMilli() / 1000.0;
            messageRepo.save(new ChatMessageEntity(sessionId, userId, "assistant", WELCOME_MESSAGE, "turn_0", now));
        }
    }

    private Map<String, Object> buildSnapshot(String sessionId) {
        SessionRuntime rt = runtimeFor(sessionId);
        List<ChatMessageEntity> rows = messageRepo.findBySessionIdOrderByIdAsc(sessionId);
        List<Map<String, Object>> messages = new ArrayList<>(rows.size());
        for (int i = 0; i < rows.size(); i++) {
            ChatMessageEntity m = rows.get(i);
            LocalDateTime dt = LocalDateTime.ofInstant(
                    Instant.ofEpochMilli((long) (m.getCreatedAt() * 1000.0)),
                    ZoneId.systemDefault()
            );
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", i);
            item.put("role", m.getRole());
            item.put("text", m.getContent());
            item.put("date", dt.format(DATE_FMT));
            item.put("time", dt.format(TIME_FMT));
            item.put("turnId", m.getTurnId());
            messages.add(item);
        }

        Map<String, Object> snap = new LinkedHashMap<>();
        snap.put("revision", rt.revision.get());
        snap.put("messages", messages);
        snap.put("bundles", List.of());
        snap.put("status", rt.status);
        snap.put("online", true);
        snap.put("speech", Map.of("phase", rt.phase, "turnId", ""));
        snap.put("presence", Map.of(
                "stage", rt.phase,
                "label", rt.status,
                "activity", "",
                "emotion", "normal",
                "motion", "idle",
                "isActive", false,
                "warning", false,
                "turnId", ""
        ));
        return snap;
    }

    private void notifySubscribers(String sessionId) {
        SessionRuntime rt = runtimeFor(sessionId);
        if (rt.subscribers.isEmpty()) return;
        String payload = objectMapper.writeValueAsString(buildSnapshot(sessionId));
        for (SseEmitter emitter : rt.subscribers) {
            try {
                emitter.send(SseEmitter.event().name("state").data(payload));
            } catch (IOException e) {
                rt.subscribers.remove(emitter);
            }
        }
    }

    private SessionRuntime runtimeFor(String sessionId) {
        return sessions.computeIfAbsent(sessionId, k -> new SessionRuntime());
    }
}
