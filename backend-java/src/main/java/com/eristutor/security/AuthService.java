package com.eristutor.security;

import com.eristutor.common.ConflictException;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.UUID;

@Service
public class AuthService {

    private final UserRepository userRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final SecureRandom secureRandom = new SecureRandom();

    public AuthService(
            UserRepository userRepository,
            RefreshTokenRepository refreshTokenRepository,
            PasswordEncoder passwordEncoder,
            JwtService jwtService
    ) {
        this.userRepository = userRepository;
        this.refreshTokenRepository = refreshTokenRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    public record AuthTokens(String accessToken, String refreshToken, String tokenType, long expiresIn, String userId, String username, String role) {}

    @Transactional
    public AuthTokens register(String username, String email, String password) {
        String cleanUser = username.trim();
        String cleanEmail = email.trim().toLowerCase();
        if (userRepository.existsByUsernameIgnoreCase(cleanUser)) {
            throw new ConflictException("username_taken", "Username already exists");
        }
        if (userRepository.existsByEmailIgnoreCase(cleanEmail)) {
            throw new ConflictException("email_taken", "Email already exists");
        }
        double now = Instant.now().toEpochMilli() / 1000.0;
        UserEntity user = new UserEntity(
                "usr_" + UUID.randomUUID().toString().replace("-", "").substring(0, 16),
                cleanUser,
                cleanEmail,
                passwordEncoder.encode(password),
                "LEARNER",
                now
        );
        userRepository.save(user);
        return issueTokenPair(user, now);
    }

    @Transactional
    public AuthTokens login(String username, String password) {
        UserEntity user = userRepository.findByUsernameIgnoreCase(username.trim())
                .orElseThrow(() -> new BadCredentialsException("Invalid username or password"));
        if (!passwordEncoder.matches(password, user.getPasswordHash())) {
            throw new BadCredentialsException("Invalid username or password");
        }
        double now = Instant.now().toEpochMilli() / 1000.0;
        return issueTokenPair(user, now);
    }

    @Transactional
    public AuthTokens refresh(String rawRefreshToken) {
        String hash = sha256Hex(rawRefreshToken);
        RefreshTokenEntity stored = refreshTokenRepository.findById(hash)
                .orElseThrow(() -> new BadCredentialsException("Invalid refresh token"));
        double now = Instant.now().toEpochMilli() / 1000.0;
        if (stored.isRevoked() || stored.getExpiresAt() <= now) {
            throw new BadCredentialsException("Refresh token expired or revoked");
        }
        stored.setRevoked(true);
        refreshTokenRepository.save(stored);
        UserEntity user = userRepository.findById(stored.getUserId())
                .orElseThrow(() -> new BadCredentialsException("User not found"));
        return issueTokenPair(user, now);
    }

    private AuthTokens issueTokenPair(UserEntity user, double now) {
        String access = jwtService.issueAccessToken(user);
        byte[] raw = new byte[32];
        secureRandom.nextBytes(raw);
        String refresh = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        String hash = sha256Hex(refresh);
        refreshTokenRepository.save(new RefreshTokenEntity(
                hash,
                user.getId(),
                now + jwtService.refreshTtlSeconds(),
                false,
                now
        ));
        return new AuthTokens(
                access,
                refresh,
                "Bearer",
                jwtService.accessTtlSeconds(),
                user.getId(),
                user.getUsername(),
                user.getRole()
        );
    }

    private static String sha256Hex(String raw) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
