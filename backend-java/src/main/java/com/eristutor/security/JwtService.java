package com.eristutor.security;

import com.eristutor.common.ErisTutorProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Date;

@Service
public class JwtService {

    private final SecretKey signingKey;
    private final long accessTtlSeconds;
    private final long refreshTtlSeconds;

    public JwtService(ErisTutorProperties properties) {
        byte[] rawSecret = properties.jwt().secret().getBytes(StandardCharsets.UTF_8);
        if (rawSecret.length < 32) {
            rawSecret = sha256Bytes(rawSecret);
        }
        this.signingKey = Keys.hmacShaKeyFor(rawSecret);
        this.accessTtlSeconds = Math.max(60, properties.jwt().accessTtlSeconds());
        this.refreshTtlSeconds = Math.max(300, properties.jwt().refreshTtlSeconds());
    }

    public String issueAccessToken(UserEntity user) {
        Instant now = Instant.now();
        return Jwts.builder()
                .subject(user.getId())
                .claim("username", user.getUsername())
                .claim("role", user.getRole())
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(accessTtlSeconds)))
                .signWith(signingKey)
                .compact();
    }

    public UserPrincipal parseAccessToken(String token) {
        Claims claims = Jwts.parser()
                .verifyWith(signingKey)
                .build()
                .parseSignedClaims(token)
                .getPayload();
        String userId = claims.getSubject();
        String username = claims.get("username", String.class);
        String role = claims.get("role", String.class);
        return new UserPrincipal(userId, username != null ? username : userId, role != null ? role : "LEARNER", false);
    }

    public long accessTtlSeconds() {
        return accessTtlSeconds;
    }

    public long refreshTtlSeconds() {
        return refreshTtlSeconds;
    }

    private static byte[] sha256Bytes(byte[] input) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(input);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
