package com.eristutor.common;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class CanonicalJsonDigestTest {

    @Test
    void keyOrderDoesNotAffectDigest() {
        Map<String, Object> m1 = new LinkedHashMap<>();
        m1.put("b", 2);
        m1.put("a", 1);
        m1.put("nested", Map.of("y", true, "x", "ok"));

        Map<String, Object> m2 = new LinkedHashMap<>();
        m2.put("nested", Map.of("x", "ok", "y", true));
        m2.put("a", 1);
        m2.put("b", 2);

        assertThat(CanonicalJsonDigest.sha256(m1))
                .hasSize(64)
                .isEqualTo(CanonicalJsonDigest.sha256(m2));
    }
}
