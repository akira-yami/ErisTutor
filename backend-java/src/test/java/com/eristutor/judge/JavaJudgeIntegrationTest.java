package com.eristutor.judge;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class JavaJudgeIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private DockerJavaRunner dockerJavaRunner;

    @Test
    void loadsJavaDiPackAndExecutesSandboxedJudgeCommittingToLearningCore() throws Exception {
        when(dockerJavaRunner.checkReadiness()).thenReturn(
                new DockerJavaRunner.ReadinessStatus(true, "ready", "", "eclipse-temurin:21")
        );
        when(dockerJavaRunner.execute(any(), any(), any())).thenReturn(
                new DockerJavaRunner.ExecutionResult(
                        "completed",
                        "correct",
                        List.of(
                                new DockerJavaRunner.TestResult("delegates", "Chuyển đúng amount tới gateway.", true),
                                new DockerJavaRunner.TestResult("returns", "Giữ kết quả true/false.", true),
                                new DockerJavaRunner.TestResult("null_guard", "Constructor từ chối null.", true)
                        ),
                        "graded",
                        "",
                        120L,
                        "eclipse-temurin:21",
                        0,
                        ""
                )
        );

        mockMvc.perform(get("/api/v1/judge/packs/java-di")
                        .header("X-Eris-Desktop-Key", "eris-desktop-local-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.groupId").value("java-di"));

        mockMvc.perform(get("/api/v1/judge/java/readiness")
                        .header("X-Eris-Desktop-Key", "eris-desktop-local-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(true));

        mockMvc.perform(post("/api/v1/judge/java/submissions?waitSeconds=5")
                        .header("X-Eris-Desktop-Key", "eris-desktop-local-key")
                        .header("X-Eris-User-Id", "judge-learner-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "requestId": "req-judge-001",
                                  "packId": "java-di",
                                  "nodeId": "javadi:v1:constructor",
                                  "exerciseId": "di.constructor.code",
                                  "code": "class OrderService { private final PaymentGateway g; OrderService(PaymentGateway g){this.g=java.util.Objects.requireNonNull(g);} boolean checkout(int a){return g.charge(a);} }"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("completed"))
                .andExpect(jsonPath("$.result").value("correct"))
                .andExpect(jsonPath("$.attemptId").isNotEmpty());

        // Verify Learning Core deterministically recorded mastery from Judge completion
        mockMvc.perform(get("/api/v1/learning/mastery/javadi:v1:constructor")
                        .header("X-Eris-Desktop-Key", "eris-desktop-local-key")
                        .header("X-Eris-User-Id", "judge-learner-1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mastery").value(0.85))
                .andExpect(jsonPath("$.correctCount").value(1));
    }
}
