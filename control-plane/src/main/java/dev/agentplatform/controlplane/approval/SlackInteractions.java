package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Optional;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Slack interactivity requests (button clicks), without HTTP, so the rules test directly.
 *
 * <p>Signature (Slack docs, "Verifying requests from Slack"): {@code X-Slack-Signature} is
 * {@code v0=} + hex HMAC-SHA256 of {@code v0:<X-Slack-Request-Timestamp>:<raw body>} under the
 * app's signing secret, compared in constant time; a timestamp more than 5 minutes from now is
 * refused, so a captured request cannot be replayed later.
 *
 * <p>Body: {@code application/x-www-form-urlencoded} with one {@code payload} field holding JSON.
 * Only {@code block_actions} clicks on the approval buttons ({@link SlackApprovalMessage}) are
 * read; anything else is ignored.
 */
public final class SlackInteractions {

    static final Duration MAX_AGE = Duration.ofMinutes(5);
    private static final String VERSION = "v0";
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private SlackInteractions() {}

    /** A click on Approve or Reject, and where the clicked message is. */
    public record Click(String runId, int step, Decision decision, String userId, ApprovalMessage message) {}

    /** True if the request is signed with {@code secret} and its timestamp is within 5 minutes of {@code now}. */
    public static boolean signatureMatches(String secret, String timestamp, byte[] body, String signature,
                                           Instant now) {
        if (timestamp == null || signature == null || !signature.startsWith(VERSION + "=")) {
            return false;
        }
        long seconds;
        try {
            seconds = Long.parseLong(timestamp.trim());
        } catch (NumberFormatException e) {
            return false;
        }
        if (Math.abs(now.getEpochSecond() - seconds) > MAX_AGE.toSeconds()) {
            return false;
        }
        byte[] presented;
        try {
            presented = HexFormat.of().parseHex(signature.substring(VERSION.length() + 1));
        } catch (IllegalArgumentException e) {
            return false;
        }
        return MessageDigest.isEqual(hmac(secret, timestamp.trim(), body), presented);
    }

    /** {@code v0=<hex>} for this request, as Slack sends it. */
    public static String sign(String secret, String timestamp, byte[] body) {
        return VERSION + "=" + HexFormat.of().formatHex(hmac(secret, timestamp, body));
    }

    /**
     * The approval click in a (verified) body, or empty if it is another interaction.
     *
     * @throws IllegalArgumentException if the body or payload is malformed
     */
    public static Optional<Click> click(byte[] body) {
        var payload = JSON.readTree(payloadField(body));
        if (!"block_actions".equals(payload.path("type").asString(""))) {
            return Optional.empty();
        }
        var action = payload.path("actions").path(0);
        if (!SlackApprovalMessage.BLOCK_ID.equals(action.path("block_id").asString(""))) {
            return Optional.empty();
        }
        var decision = switch (action.path("action_id").asString("")) {
            case SlackApprovalMessage.APPROVE -> Decision.approved;
            case SlackApprovalMessage.REJECT -> Decision.rejected;
            default -> null;
        };
        if (decision == null) {
            return Optional.empty();
        }
        JsonNode value;
        try {
            value = JSON.readTree(action.path("value").asString(""));
        } catch (JacksonException e) {
            throw new IllegalArgumentException("Button value is not JSON");
        }
        var runId = value.path("run_id").asString("");
        var step = value.path("step").asInt(0);
        var userId = payload.path("user").path("id").asString("");
        var channel = payload.path("container").path("channel_id").asString(payload.path("channel").path("id").asString(""));
        var ts = payload.path("container").path("message_ts").asString(payload.path("message").path("ts").asString(""));
        if (runId.isEmpty() || step < 1 || userId.isEmpty() || channel.isEmpty() || ts.isEmpty()) {
            throw new IllegalArgumentException("Approval click is missing run, step, user, channel or message");
        }
        return Optional.of(new Click(runId, step, decision, userId, new ApprovalMessage(channel, ts)));
    }

    private static String payloadField(byte[] body) {
        for (var pair : new String(body, StandardCharsets.UTF_8).split("&")) {
            if (pair.startsWith("payload=")) {
                return URLDecoder.decode(pair.substring("payload=".length()), StandardCharsets.UTF_8);
            }
        }
        throw new IllegalArgumentException("Body has no payload field");
    }

    private static byte[] hmac(String secret, String timestamp, byte[] body) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            mac.update((VERSION + ":" + timestamp + ":").getBytes(StandardCharsets.UTF_8));
            return mac.doFinal(body);
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            throw new IllegalStateException(e);
        }
    }
}
