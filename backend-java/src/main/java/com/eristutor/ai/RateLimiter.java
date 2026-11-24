package com.eristutor.ai;

public interface RateLimiter {
    record Decision(boolean allowed, long remaining, long retryAfterSeconds) {}

    Decision tryConsume(String key);
}
