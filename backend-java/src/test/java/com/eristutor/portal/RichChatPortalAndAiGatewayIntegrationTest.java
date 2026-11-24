package com.eristutor.portal;

import com.eristutor.ai.RateLimiter;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class RichChatPortalAndAiGatewayIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private RateLimiter rateLimiter;

    @Test
    void servesRichChatPortalStateSendHistoryAndSseStream() throws Exception {
        mockMvc.perform(get("/api/state"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.online").value(true))
                .andExpect(jsonPath("$.messages[0].role").value("assistant"));

        mockMvc.perform(post("/api/send")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"message\":\"Dependency Injection là gì vậy Eris?\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true))
                .andExpect(jsonPath("$.provider").value("sandbox-fallback"));

        mockMvc.perform(get("/api/state"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.messages.length()").value(3))
                .andExpect(jsonPath("$.messages[2].text", containsString("Nội tâm của Eris")));

        mockMvc.perform(post("/api/history/delete"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true));

        // Verify SSE endpoint streams initial state
        mockMvc.perform(get("/api/events").accept(MediaType.TEXT_EVENT_STREAM))
                .andExpect(status().isOk());
    }

    @Test
    void servesRichChatIndexHtmlAndActuatorHealth() throws Exception {
        mockMvc.perform(get("/actuator/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"));

        mockMvc.perform(get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("ErisTutor Java Backend API")));
    }
}
