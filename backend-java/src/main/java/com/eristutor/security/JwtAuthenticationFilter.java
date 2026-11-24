package com.eristutor.security;

import com.eristutor.common.ErisTutorProperties;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    public static final String DESKTOP_KEY_HEADER = "X-Eris-Desktop-Key";
    public static final String DESKTOP_USER_HEADER = "X-Eris-User-Id";

    private final JwtService jwtService;
    private final String desktopApiKey;

    public JwtAuthenticationFilter(JwtService jwtService, ErisTutorProperties properties) {
        this.jwtService = jwtService;
        this.desktopApiKey = properties.desktop() != null && properties.desktop().apiKey() != null
                ? properties.desktop().apiKey()
                : "";
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {
        if (SecurityContextHolder.getContext().getAuthentication() == null) {
            String desktopHeader = request.getHeader(DESKTOP_KEY_HEADER);
            String authHeader = request.getHeader(HttpHeaders.AUTHORIZATION);

            if (desktopHeader != null && !desktopApiKey.isBlank() && constantTimeEquals(desktopHeader, desktopApiKey)) {
                String requestedUser = request.getHeader(DESKTOP_USER_HEADER);
                String userId = (requestedUser != null && !requestedUser.isBlank() && requestedUser.matches("[A-Za-z0-9_.:-]{1,64}"))
                        ? requestedUser.trim()
                        : "desktop-eris";
                UserPrincipal principal = new UserPrincipal(userId, "desktop-eris", "DESKTOP", true);
                setAuthentication(principal);
            } else if (authHeader != null && authHeader.startsWith("Bearer ")) {
                String token = authHeader.substring(7).trim();
                if (!token.isEmpty()) {
                    try {
                        UserPrincipal principal = jwtService.parseAccessToken(token);
                        setAuthentication(principal);
                    } catch (JwtException | IllegalArgumentException ignored) {
                        SecurityContextHolder.clearContext();
                    }
                }
            }
        }
        filterChain.doFilter(request, response);
    }

    private void setAuthentication(UserPrincipal principal) {
        var auth = new UsernamePasswordAuthenticationToken(
                principal,
                null,
                List.of(new SimpleGrantedAuthority("ROLE_" + principal.role()))
        );
        SecurityContextHolder.getContext().setAuthentication(auth);
    }

    private static boolean constantTimeEquals(String a, String b) {
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }
}
