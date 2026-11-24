package com.eristutor.common;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.SerializationFeature;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;

/**
 * Deterministic SHA-256 over canonical JSON (sorted keys, compact separators)
 * matching ErisTutor Python's {@code records.digest()}.
 */
public final class CanonicalJsonDigest {

    private static final ObjectMapper CANONICAL_MAPPER = JsonMapper.builder()
            .disable(SerializationFeature.INDENT_OUTPUT)
            .build();

    private CanonicalJsonDigest() {}

    public static String sha256(Object value) {
        try {
            JsonNode tree = CANONICAL_MAPPER.valueToTree(value);
            JsonNode sorted = sortKeysRecursively(tree);
            String canonical = CANONICAL_MAPPER.writeValueAsString(sorted);
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(canonical.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    private static JsonNode sortKeysRecursively(JsonNode node) {
        if (node == null || node.isValueNode() || node.isNull()) {
            return node;
        }
        if (node.isArray()) {
            ArrayNode arr = CANONICAL_MAPPER.createArrayNode();
            for (JsonNode child : node) {
                arr.add(sortKeysRecursively(child));
            }
            return arr;
        }
        if (node.isObject()) {
            ObjectNode obj = CANONICAL_MAPPER.createObjectNode();
            List<String> keys = new ArrayList<>();
            node.propertyNames().forEach(keys::add);
            Collections.sort(keys);
            for (String key : keys) {
                obj.set(key, sortKeysRecursively(node.get(key)));
            }
            return obj;
        }
        return node;
    }
}
