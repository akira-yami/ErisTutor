package com.eristutor.portal;

import com.eristutor.security.SecurityUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;

@RestController
@Tag(name = "RichChat Cloud Portal", description = "Drop-in RichChat Studio endpoints and versioned multi-user chat API")
public class RichChatPortalController {

    private static final String PUBLIC_SESSION = "public-studio";

    private final ChatPortalService portalService;

    public RichChatPortalController(ChatPortalService portalService) {
        this.portalService = portalService;
    }

    public record SendChatRequest(String message, String sessionId) {}

    // --- Drop-in compatibility endpoints for src/body/richchat/ ---

    @GetMapping("/api/state")
    @Operation(summary = "Get current RichChat Studio state snapshot (cloud_server.py compatible)")
    public Map<String, Object> publicState() {
        return portalService.getSnapshot(PUBLIC_SESSION, "public-guest");
    }

    @PostMapping("/api/send")
    @Operation(summary = "Send a chat message to Eris Socratic AI Gateway (cloud_server.py compatible)")
    public ResponseEntity<Map<String, Object>> publicSend(@RequestBody SendChatRequest req) {
        String msg = req != null && req.message() != null ? req.message().trim() : "";
        if (msg.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("ok", false, "error", "empty message"));
        }
        return ResponseEntity.ok(portalService.sendMessage(PUBLIC_SESSION, "public-guest", msg));
    }

    @PostMapping("/api/feedback")
    public Map<String, Object> feedback() {
        return Map.of("ok", true);
    }

    @PostMapping("/api/history/delete")
    @Operation(summary = "Clear public RichChat history")
    public Map<String, Object> publicDeleteHistory() {
        return portalService.deleteHistory(PUBLIC_SESSION);
    }

    @GetMapping(value = "/api/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary = "Subscribe to real-time RichChat state SSE stream")
    public SseEmitter publicEvents() {
        return portalService.subscribe(PUBLIC_SESSION, "public-guest");
    }

    // --- Authenticated multi-user versioned endpoints ---

    @GetMapping("/api/v1/chat/state")
    @Operation(summary = "Get authenticated learner chat state")
    public Map<String, Object> userState(@RequestParam(defaultValue = "default") String sessionId) {
        String userId = SecurityUtils.currentUserId();
        return portalService.getSnapshot(userId + ":" + sessionId, userId);
    }

    @PostMapping("/api/v1/chat/send")
    @Operation(summary = "Send authenticated Socratic chat message with learner context injection")
    public Map<String, Object> userSend(@RequestBody SendChatRequest req) {
        String userId = SecurityUtils.currentUserId();
        String sid = (req.sessionId() == null || req.sessionId().isBlank()) ? "default" : req.sessionId().trim();
        return portalService.sendMessage(userId + ":" + sid, userId, req.message());
    }

    @DeleteMapping("/api/v1/chat/history")
    @Operation(summary = "Clear authenticated learner chat history")
    public Map<String, Object> userDeleteHistory(@RequestParam(defaultValue = "default") String sessionId) {
        String userId = SecurityUtils.currentUserId();
        return portalService.deleteHistory(userId + ":" + sessionId);
    }

    @GetMapping(value = "/api/v1/chat/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary = "Stream authenticated learner chat SSE events")
    public SseEmitter userEvents(@RequestParam(defaultValue = "default") String sessionId) {
        String userId = SecurityUtils.currentUserId();
        return portalService.subscribe(userId + ":" + sessionId, userId);
    }
}
