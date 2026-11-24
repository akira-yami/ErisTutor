package com.eristutor.common;

public class DomainValidationException extends RuntimeException {
    private final String code;

    public DomainValidationException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
