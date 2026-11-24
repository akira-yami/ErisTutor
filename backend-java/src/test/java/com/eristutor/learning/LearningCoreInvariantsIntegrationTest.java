package com.eristutor.learning;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import static org.hamcrest.Matchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class LearningCoreInvariantsIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void enforcesCasRevisionsIdempotencyCorrectionAndForgetTombstones() throws Exception {
        String desktopKey = "eris-desktop-local-key";
        String userHeader = "learner-invariants-1";

        // 1. Register DAG path
        MvcResult pathRes = mockMvc.perform(post("/api/v1/learning/paths")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "pathId": "javadi:test:path",
                                  "title": "Java DI Test Path",
                                  "goal": "Master DI",
                                  "knowledgePoints": [
                                    {"kpId": "javadi:test:dep", "title": "Dependency", "kpType": "concept", "prerequisites": []},
                                    {"kpId": "javadi:test:ctor", "title": "Constructor DI", "kpType": "procedure", "prerequisites": ["javadi:test:dep"]}
                                  ]
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.revision").isNotEmpty())
                .andReturn();

        String rev1 = objectMapper.readTree(pathRes.getResponse().getContentAsString()).get("revision").asText();

        // 2. Stale CAS revision -> 409 Conflict
        mockMvc.perform(post("/api/v1/learning/paths")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "pathId": "javadi:test:path",
                                  "title": "Updated Title",
                                  "expectedRevision": "stale_revision_123",
                                  "knowledgePoints": [
                                    {"kpId": "javadi:test:dep", "title": "Dependency", "kpType": "concept", "prerequisites": []}
                                  ]
                                }
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("stale_revision"));

        // Matching CAS revision succeeds
        mockMvc.perform(post("/api/v1/learning/paths")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "pathId": "javadi:test:path",
                                  "title": "Updated Title",
                                  "expectedRevision": "%s",
                                  "knowledgePoints": [
                                    {"kpId": "javadi:test:dep", "title": "Dependency", "kpType": "concept", "prerequisites": []},
                                    {"kpId": "javadi:test:ctor", "title": "Constructor DI", "kpType": "procedure", "prerequisites": ["javadi:test:dep"]}
                                  ]
                                }
                                """.formatted(rev1)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.title").value("Updated Title"));

        // 3. Idempotent attempt recording & conflict detection
        String attemptPayload = """
                {
                  "attemptId": "att-inv-1",
                  "kpId": "javadi:test:dep",
                  "result": "correct",
                  "evidenceType": "quiz_correct",
                  "itemFamilyId": "q1"
                }
                """;
        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(attemptPayload))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.idempotentReplay").value(false))
                .andExpect(jsonPath("$.mastery.mastery").value(0.95));

        // Exact retry -> idempotentReplay = true
        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(attemptPayload))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.idempotentReplay").value(true));

        // Same attemptId with conflicting payload -> 409 Conflict
        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "attemptId": "att-inv-1",
                                  "kpId": "javadi:test:dep",
                                  "result": "incorrect",
                                  "evidenceType": "quiz_incorrect",
                                  "itemFamilyId": "q1"
                                }
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("attempt_conflict_use_explicit_correction"));

        // 4. Hinted attempt does NOT inflate mastery
        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "attemptId": "att-hint-1",
                                  "kpId": "javadi:test:ctor",
                                  "result": "correct",
                                  "evidenceType": "quiz_correct",
                                  "hintCount": 1
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.assistance").value("hinted"))
                .andExpect(jsonPath("$.mastery.mastery").value(0.0));

        // 5. Explicit correction supersedes original attempt & recomputes mastery
        mockMvc.perform(post("/api/v1/learning/attempts/att-inv-1/correct")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "correctionId": "att-inv-1-fix",
                                  "result": "incorrect",
                                  "evidenceType": "quiz_incorrect"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("corrected"))
                .andExpect(jsonPath("$.mastery.mastery").value(0.0));

        // 6. Forget attempt writes tombstone and blocks replay resurrection
        mockMvc.perform(delete("/api/v1/learning/attempts/att-inv-1-fix")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.attemptCount").value(0));

        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", desktopKey)
                        .header("X-Eris-User-Id", userHeader)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "attemptId": "att-inv-1-fix",
                                  "kpId": "javadi:test:dep",
                                  "result": "correct",
                                  "evidenceType": "quiz_correct"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ignored_forgotten"));
    }
}
