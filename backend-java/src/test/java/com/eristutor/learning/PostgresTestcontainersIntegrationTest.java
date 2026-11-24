package com.eristutor.learning;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers(disabledWithoutDocker = true)
class PostgresTestcontainersIntegrationTest {

    @Container
    static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer("postgres:16-alpine")
            .withDatabaseName("eristutor")
            .withUsername("eristutor")
            .withPassword("eristutor");

    @DynamicPropertySource
    static void configureDatasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("spring.datasource.driver-class-name", () -> "org.postgresql.Driver");
        registry.add("spring.jpa.properties.hibernate.dialect", () -> "org.hibernate.dialect.PostgreSQLDialect");
        registry.add("eristutor.rate-limit.backend", () -> "memory");
        registry.add("management.health.redis.enabled", () -> "false");
    }

    @Autowired
    private MockMvc mockMvc;

    @Test
    void flywayAndAcidLedgerWorkOnRealPostgreSql16() throws Exception {
        mockMvc.perform(post("/api/v1/learning/attempts")
                        .header("X-Eris-Desktop-Key", "eris-desktop-local-key")
                        .header("X-Eris-User-Id", "pg-user-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "attemptId": "pg-att-1",
                                  "kpId": "javadi:v1:dependency",
                                  "result": "correct",
                                  "evidenceType": "quiz_correct"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mastery.mastery").value(0.95));
    }
}
