package com.eristutor.ai;

import com.eristutor.common.ErisTutorProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

@Component
@ConditionalOnProperty(name = "eristutor.rate-limit.backend", havingValue = "memory", matchIfMissing = true)
public class InMemoryTokenBucketRateLimiter implements RateLimiter {

    private static final class Bucket {
        double tokens;
        long lastRefillNanos;

        Bucket(double tokens, long lastRefillNanos) {
            this.tokens = tokens;
            this.lastRefillNanos = lastRefillNanos;
        }
    }

    private final int capacity;
    private final double refillPerSecond;
    private final ConcurrentMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public InMemoryTokenBucketRateLimiter(ErisTutorProperties properties) {
        int rpm = properties.rateLimit() != null && properties.rateLimit().requestsPerMinute() > 0
                ? properties.rateLimit().requestsPerMinute()
                : 30;
        this.capacity = rpm;
        this.refillPerSecond = rpm / 60.0;
    }

    @Override
    public Decision tryConsume(String key) {
        long now = System.nanoTime();
        Bucket bucket = buckets.computeIfAbsent(key, k -> new Bucket(capacity, now));
        synchronized (bucket) {
            double elapsedSeconds = Math.max(0.0, (now - bucket.lastRefillNanos) / 1_000_000_000.0);
            bucket.tokens = Math.min(capacity, bucket.tokens + elapsedSeconds * refillPerSecond);
            bucket.lastRefillNanos = now;
            if (bucket.tokens >= 1.0) {
                bucket.tokens -= 1.0;
                return new Decision(true, (long) Math.floor(bucket.tokens), 0L);
            }
            long retryAfter = Math.max(1L, (long) Math.ceil((1.0 - bucket.tokens) / refillPerSecond));
            return new Decision(false, 0L, retryAfter);
        }
    }
}
