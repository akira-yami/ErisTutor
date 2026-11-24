package com.eristutor.ai;

import com.eristutor.common.ErisTutorProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
@ConditionalOnProperty(name = "eristutor.rate-limit.backend", havingValue = "redis")
public class RedisTokenBucketRateLimiter implements RateLimiter {

    private final StringRedisTemplate redisTemplate;
    private final int requestsPerMinute;

    public RedisTokenBucketRateLimiter(StringRedisTemplate redisTemplate, ErisTutorProperties properties) {
        this.redisTemplate = redisTemplate;
        this.requestsPerMinute = properties.rateLimit() != null && properties.rateLimit().requestsPerMinute() > 0
                ? properties.rateLimit().requestsPerMinute()
                : 30;
    }

    @Override
    public Decision tryConsume(String key) {
        long minuteBucket = System.currentTimeMillis() / 60_000L;
        String redisKey = "eristutor:rl:" + key + ":" + minuteBucket;
        Long count = redisTemplate.opsForValue().increment(redisKey);
        if (count != null && count == 1L) {
            redisTemplate.expire(redisKey, Duration.ofSeconds(65));
        }
        long current = count != null ? count : 1L;
        if (current <= requestsPerMinute) {
            return new Decision(true, Math.max(0L, requestsPerMinute - current), 0L);
        }
        Long ttl = redisTemplate.getExpire(redisKey);
        long retry = (ttl != null && ttl > 0) ? ttl : 60L;
        return new Decision(false, 0L, retry);
    }
}
