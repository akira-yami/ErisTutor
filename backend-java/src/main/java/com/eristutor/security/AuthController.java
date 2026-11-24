package com.eristutor.security;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/auth")
@Tag(name = "Authentication", description = "JWT registration, login, and refresh token rotation")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    public record RegisterRequest(
            @NotBlank @Size(min = 3, max = 60) String username,
            @NotBlank @Email @Size(max = 160) String email,
            @NotBlank @Size(min = 6, max = 120) String password
    ) {}

    public record LoginRequest(
            @NotBlank String username,
            @NotBlank String password
    ) {}

    public record RefreshRequest(@NotBlank String refreshToken) {}

    @PostMapping("/register")
    @ResponseStatus(HttpStatus.CREATED)
    @Operation(summary = "Register a new learner account")
    public AuthService.AuthTokens register(@Valid @RequestBody RegisterRequest req) {
        return authService.register(req.username(), req.email(), req.password());
    }

    @PostMapping("/login")
    @Operation(summary = "Authenticate with username/password and receive JWT + refresh token")
    public AuthService.AuthTokens login(@Valid @RequestBody LoginRequest req) {
        return authService.login(req.username(), req.password());
    }

    @PostMapping("/refresh")
    @Operation(summary = "Rotate refresh token and issue a new JWT pair")
    public AuthService.AuthTokens refresh(@Valid @RequestBody RefreshRequest req) {
        return authService.refresh(req.refreshToken());
    }

    @GetMapping("/me")
    @Operation(summary = "Inspect the current authenticated principal")
    public UserPrincipal me(@AuthenticationPrincipal UserPrincipal principal) {
        return principal;
    }
}
