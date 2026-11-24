package com.eristutor.security;

public record UserPrincipal(String userId, String username, String role, boolean desktopService) {
}
