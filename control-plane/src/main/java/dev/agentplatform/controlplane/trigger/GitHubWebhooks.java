package dev.agentplatform.controlplane.trigger;

import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * GitHub webhook rules, kept free of HTTP so they are easy to test: signature check, event to
 * trigger name, and the run input built from a payload.
 */
public final class GitHubWebhooks {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String SIGNATURE_PREFIX = "sha256=";

    /** GitHub event names that differ from the object name used in triggers. */
    private static final Map<String, String> TRIGGER_OBJECT = Map.of("issues", "issue");

    private GitHubWebhooks() {}

    /**
     * True if {@code header} ({@code X-Hub-Signature-256}) is the HMAC-SHA256 of the raw body under
     * {@code secret}. Compared in constant time.
     */
    public static boolean signatureMatches(String secret, byte[] body, String header) {
        if (header == null || !header.startsWith(SIGNATURE_PREFIX)) {
            return false;
        }
        byte[] presented;
        try {
            presented = HexFormat.of().parseHex(header.substring(SIGNATURE_PREFIX.length()));
        } catch (IllegalArgumentException e) {
            return false;
        }
        return MessageDigest.isEqual(hmac(secret, body), presented);
    }

    /** {@code sha256=<hex>} for this body, as GitHub sends it. */
    public static String sign(String secret, byte[] body) {
        return SIGNATURE_PREFIX + HexFormat.of().formatHex(hmac(secret, body));
    }

    /**
     * Trigger name for an event: {@code github.<object>.<action>}, or {@code github.<object>} when
     * the payload has no action. {@code issues} + {@code opened} is {@code github.issue.opened}.
     */
    public static String triggerName(String event, JsonNode payload) {
        var object = TRIGGER_OBJECT.getOrDefault(event, event);
        var action = payload.path("action");
        return action.isString() && !action.asString().isBlank()
                ? "github." + object + "." + action.asString()
                : "github." + object;
    }

    /**
     * The run input for an event. Issue events are trimmed to the fields an agent needs (the full
     * payload is 10-30 KB, mostly URLs, and the model reads the input on every step); other events
     * pass through whole for now.
     */
    public static ObjectNode runInput(String event, ObjectNode payload) {
        if (!"issues".equals(event)) {
            return payload;
        }
        var input = JSON.createObjectNode();
        copy(payload, input, "action");
        var repository = payload.path("repository");
        if (repository.isObject()) {
            var repo = input.putObject("repository");
            copy(repository, repo, "full_name");
        }
        var issue = payload.path("issue");
        if (issue.isObject()) {
            var out = input.putObject("issue");
            for (var field : new String[] {"number", "title", "body", "state", "html_url"}) {
                copy(issue, out, field);
            }
            var labels = out.putArray("labels");
            for (var label : issue.path("labels")) {
                if (label.path("name").isString()) {
                    labels.add(label.path("name").asString());
                }
            }
            login(issue.path("user"), out, "user");
        }
        login(payload.path("sender"), input, "sender");
        return input;
    }

    private static void copy(JsonNode from, ObjectNode to, String field) {
        var value = from.get(field);
        if (value != null) {
            to.set(field, value);
        }
    }

    private static void login(JsonNode user, ObjectNode to, String field) {
        if (user.path("login").isString()) {
            to.putObject(field).put("login", user.path("login").asString());
        }
    }

    private static byte[] hmac(String secret, byte[] body) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(body);
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            throw new IllegalStateException("HmacSHA256 unavailable", e);
        }
    }
}
