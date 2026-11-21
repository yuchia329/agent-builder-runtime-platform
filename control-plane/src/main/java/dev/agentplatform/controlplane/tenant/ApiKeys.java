package dev.agentplatform.controlplane.tenant;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;
import java.util.regex.Pattern;

/**
 * Tenant API keys: {@code ap_} and 32 random bytes in unpadded base64url (46 characters). Only the
 * SHA-256 of a key is stored; for random keys this long a fast hash is enough (a slow hash is for
 * guessable passwords). The prefix (first 10 characters) names a key in logs and listings without
 * revealing it. Never log a full key.
 */
public final class ApiKeys {

    public static final String PREFIX = "ap_";
    private static final Pattern SHAPE = Pattern.compile("^ap_[A-Za-z0-9_-]{43}$");
    private static final SecureRandom RANDOM = new SecureRandom();

    private ApiKeys() {}

    /** A new key with its hash and prefix. Show {@code key} once; store only the rest. */
    public record NewApiKey(String key, String hash, String prefix) {

        @Override
        public String toString() {
            return "NewApiKey[prefix=" + prefix + "]"; // never the key
        }
    }

    public static NewApiKey generate() {
        var bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        var key = PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        return new NewApiKey(key, hash(key), prefix(key));
    }

    /** True for a key this class could have made (cheap check before any store lookup). */
    public static boolean isWellFormed(String key) {
        return key != null && SHAPE.matcher(key).matches();
    }

    /** Lowercase hex SHA-256 of the key's UTF-8 bytes. */
    public static String hash(String key) {
        try {
            var digest = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is always available", e);
        }
    }

    public static String prefix(String key) {
        return key.substring(0, Math.min(10, key.length()));
    }
}
