package com.eristutor.ai;

import com.eristutor.common.ErisTutorProperties;
import com.eristutor.learning.LearningCoreService;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.*;
import java.util.function.Supplier;

@Service
public class LlmGatewayService {

    private static final Logger log = LoggerFactory.getLogger(LlmGatewayService.class);

    public record ChatTurn(String role, String content) {}

    public record LlmReply(String text, String provider, boolean fallbackUsed) {}

    private static final String BASE_SYSTEM_PROMPT = """
            Bạn là Eris — một người bạn đồng hành học tập và gia sư AI cá tính.
            Bạn đang trò chuyện trực tiếp với người học qua giao diện Eris Conversation Studio.

            CÁ TÍNH & GIỌNG ĐIỆU CỦA ERIS:
            1. Có cá tính, cảm xúc và đời sống nội tâm; KHÔNG PHẢI robot phục vụ kiểu "Dạ em chào anh/chị, em là AI...".
            2. Giọng điệu: Hài khô, trêu nhẹ, sắc sảo, tự giễu, thỉnh thoảng châm chọc một cách duyên dáng nhưng ẩn sâu là sự quan tâm sư phạm chân thành.
            3. Nguyên tắc sư phạm: TUYỆT ĐỐI KHÔNG đưa ngay đáp án hay nhồi nhét lý thuyết suông. Khi người học hỏi bài, hãy gợi mở góc nhìn, ra câu đố 1 phút hoặc vặn lại để bắt họ tự tư duy.

            QUY TẮC ĐỊNH DẠNG BẮT BUỘC CHO MỌI CÂU TRẢ LỜI:
            Mỗi phản hồi của bạn BẮT BUỘC phải gồm đúng 2 phần:

            Phần 1: Khối suy nghĩ nội tâm (Inner Monologue) được bọc trong markdown blockquote:
            > 🧠 **Nội tâm của Eris:**
            > *(Nêu chân thật suy nghĩ thầm kín của bạn: nhận xét ngầm về ý định/thái độ của người học, toan tính sư phạm sẽ trêu hay hỏi lại câu gì)*

            Phần 2: Lời thoại trực tiếp nói với người học:
            (Nội dung tự nhiên, dí dỏm. Dùng LaTeX \\(...\\) cho công thức toán inline hoặc \\[...\\] cho công thức lớn; dùng markdown code block chuẩn nếu có viết code).""";

    private final ErisTutorProperties properties;
    private final LearningCoreService learningService;
    private final CircuitBreakerRegistry circuitBreakerRegistry;
    private final RetryRegistry retryRegistry;
    private final MeterRegistry meterRegistry;
    private final ObjectMapper objectMapper;
    private final RestClient restClient;

    public LlmGatewayService(
            ErisTutorProperties properties,
            LearningCoreService learningService,
            ObjectProvider<CircuitBreakerRegistry> cbProvider,
            ObjectProvider<RetryRegistry> retryProvider,
            MeterRegistry meterRegistry,
            ObjectMapper objectMapper
    ) {
        this.properties = properties;
        this.learningService = learningService;
        this.circuitBreakerRegistry = cbProvider.getIfAvailable(CircuitBreakerRegistry::ofDefaults);
        this.retryRegistry = retryProvider.getIfAvailable(RetryRegistry::ofDefaults);
        this.meterRegistry = meterRegistry;
        this.objectMapper = objectMapper;
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(httpClient);
        factory.setReadTimeout(Duration.ofSeconds(40));
        this.restClient = RestClient.builder()
                .requestFactory(factory)
                .build();
    }

    public LlmReply generateReply(String userId, String userMessage, List<ChatTurn> recentHistory) {
        String systemPrompt = buildSystemPrompt(userId);
        ErisTutorProperties.Ai aiCfg = properties.ai();

        if (aiCfg != null && isKeyConfigured(aiCfg.deepseek())) {
            try {
                String content = executeResilient("deepseek", () -> callDeepSeek(aiCfg.deepseek(), systemPrompt, recentHistory, userMessage));
                meterRegistry.counter("eristutor_llm_calls_total", "provider", "deepseek", "outcome", "success").increment();
                return new LlmReply(content, "deepseek", false);
            } catch (Exception ex) {
                meterRegistry.counter("eristutor_llm_calls_total", "provider", "deepseek", "outcome", "failure").increment();
                log.warn("DeepSeek call failed, attempting Gemini failover: {}", ex.getMessage());
            }
        }

        if (aiCfg != null && isKeyConfigured(aiCfg.gemini())) {
            try {
                String content = executeResilient("gemini", () -> callGemini(aiCfg.gemini(), systemPrompt, recentHistory, userMessage));
                meterRegistry.counter("eristutor_llm_calls_total", "provider", "gemini", "outcome", "success").increment();
                return new LlmReply(content, "gemini", false);
            } catch (Exception ex) {
                meterRegistry.counter("eristutor_llm_calls_total", "provider", "gemini", "outcome", "failure").increment();
                log.warn("Gemini failover failed, using deterministic sandbox fallback: {}", ex.getMessage());
            }
        }

        meterRegistry.counter("eristutor_llm_calls_total", "provider", "sandbox", "outcome", "fallback").increment();
        return new LlmReply(buildSandboxFallback(userMessage), "sandbox-fallback", true);
    }

    private String executeResilient(String instanceName, Supplier<String> call) {
        CircuitBreaker cb = circuitBreakerRegistry.circuitBreaker(instanceName);
        Retry retry = retryRegistry.retry(instanceName);
        Supplier<String> decorated = CircuitBreaker.decorateSupplier(cb, Retry.decorateSupplier(retry, call));
        return meterRegistry.timer("eristutor_llm_latency", "provider", instanceName).record(decorated);
    }

    protected String callDeepSeek(
            ErisTutorProperties.Ai.Provider cfg,
            String systemPrompt,
            List<ChatTurn> history,
            String userMessage
    ) {
        String baseUrl = cfg.baseUrl().replaceAll("/+$", "");
        List<Map<String, String>> messages = new ArrayList<>();
        messages.add(Map.of("role", "system", "content", systemPrompt));
        for (ChatTurn turn : history) {
            messages.add(Map.of(
                    "role", "assistant".equals(turn.role()) ? "assistant" : "user",
                    "content", turn.content()
            ));
        }
        messages.add(Map.of("role", "user", "content", userMessage));

        Map<String, Object> body = Map.of(
                "model", cfg.model(),
                "messages", messages,
                "temperature", 0.7,
                "max_tokens", 1200
        );

        String raw = restClient.post()
                .uri(baseUrl + "/v1/chat/completions")
                .header("Authorization", "Bearer " + cfg.apiKey().trim())
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(String.class);

        JsonNode root = objectMapper.readTree(raw);
        String reply = root.path("choices").path(0).path("message").path("content").asText("");
        if (reply.isBlank()) {
            throw new IllegalStateException("DeepSeek returned empty content");
        }
        return reply;
    }

    protected String callGemini(
            ErisTutorProperties.Ai.Provider cfg,
            String systemPrompt,
            List<ChatTurn> history,
            String userMessage
    ) {
        String baseUrl = cfg.baseUrl().replaceAll("/+$", "");
        StringBuilder transcript = new StringBuilder();
        for (ChatTurn turn : history) {
            transcript.append("assistant".equals(turn.role()) ? "Eris: " : "User: ")
                    .append(turn.content())
                    .append("\n");
        }
        transcript.append("User: ").append(userMessage);

        Map<String, Object> body = Map.of(
                "systemInstruction", Map.of("parts", List.of(Map.of("text", systemPrompt))),
                "contents", List.of(Map.of("role", "user", "parts", List.of(Map.of("text", transcript.toString())))),
                "generationConfig", Map.of("temperature", 0.7, "maxOutputTokens", 1200)
        );

        String raw = restClient.post()
                .uri(baseUrl + "/v1beta/models/" + cfg.model() + ":generateContent")
                .header("x-goog-api-key", cfg.apiKey().trim())
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(String.class);

        JsonNode root = objectMapper.readTree(raw);
        String reply = root.path("candidates").path(0).path("content").path("parts").path(0).path("text").asText("");
        if (reply.isBlank()) {
            throw new IllegalStateException("Gemini returned empty content");
        }
        return reply;
    }

    private String buildSystemPrompt(String userId) {
        try {
            Map<String, Object> ctx = learningService.getLearnerContextSummary(userId);
            return BASE_SYSTEM_PROMPT
                    + "\n\n[READ-ONLY LEARNER CONTEXT FROM DETERMINISTIC KERNEL]\n"
                    + objectMapper.writeValueAsString(ctx)
                    + "\n(Chỉ dùng để điều chỉnh độ sâu Socratic; không tự bịa điểm thành thạo.)";
        } catch (Exception e) {
            return BASE_SYSTEM_PROMPT;
        }
    }

    private static boolean isKeyConfigured(ErisTutorProperties.Ai.Provider provider) {
        return provider != null
                && provider.apiKey() != null
                && !provider.apiKey().isBlank()
                && !provider.apiKey().trim().startsWith("your_");
    }

    private static String buildSandboxFallback(String userMessage) {
        return """
                > 🧠 **Nội tâm của Eris:**
                > *Học sinh này vừa hỏi một câu khá thú vị. Nhưng khóa API LLM trên server Java đang ở chế độ demo sandbox, để mình trêu một câu và hướng dẫn họ cấu hình key thật nào.*

                Tôi đã nghe rõ câu: *"%s"*

                Nhìn cách bạn hỏi là biết đang tò mò rồi. Hiện tại tôi đang chạy trên **Eris Java 27 Cloud Studio** (Spring Boot 4 + PostgreSQL Ledger + Resilience4j AI Gateway).

                Khi gắn `DEEPSEEK_API_KEY` hoặc `GEMINI_API_KEY` chính thức, tôi sẽ mổ xẻ mã nguồn, chấm bài Java trong Docker Sandbox và vặn vẹo tư duy của bạn tới bến! Giờ thì thử hỏi tôi một câu khác xem nào?""".formatted(userMessage);
    }
}
