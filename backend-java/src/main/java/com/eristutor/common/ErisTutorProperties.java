package com.eristutor.common;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "eristutor")
public record ErisTutorProperties(
        Jwt jwt,
        Desktop desktop,
        Richchat richchat,
        Packs packs,
        Judge judge,
        Ai ai,
        RateLimit rateLimit
) {
    public record Jwt(String secret, long accessTtlSeconds, long refreshTtlSeconds) {}
    public record Desktop(String apiKey) {}
    public record Richchat(String dir) {}
    public record Packs(String dir) {}
    public record Judge(String dockerImage, int maxConcurrent, int timeoutSeconds, String workspaceDir) {}
    public record Ai(Provider deepseek, Provider gemini) {
        public record Provider(String baseUrl, String apiKey, String model, int timeoutSeconds) {}
    }
    public record RateLimit(String backend, int requestsPerMinute) {}
}
