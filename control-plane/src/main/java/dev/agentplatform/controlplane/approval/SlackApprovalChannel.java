package dev.agentplatform.controlplane.approval;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.function.Supplier;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

/**
 * {@link ApprovalChannel} on Slack: posts the request to one channel with {@code chat.postMessage}
 * and shows the outcome with {@code chat.update} (bot token, scope {@code chat:write}, bot invited
 * to the channel). Bodies: {@link SlackApprovalMessage}.
 *
 * <p>Recorded behavior: Slack answers HTTP 200 for errors too, with {@code {"ok": false, "error"}}
 * ({@code channel_not_found}, {@code invalid_blocks}; a malformed token gave {@code internal_error},
 * not {@code invalid_auth}), so success is {@code ok: true}, never the status alone. 429 carries
 * {@code Retry-After}. Nothing is retried here: the caller's sweep posts again later.
 *
 * <p>Timeouts are short (connect 2 s, answer 3 s) because the post happens inside the worker's
 * status report. No connection is opened at startup (Lambda SnapStart).
 */
public class SlackApprovalChannel implements ApprovalChannel {

    public static final String DEFAULT_API_URL = "https://slack.com/api";
    static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(2);
    static final Duration ANSWER_TIMEOUT = Duration.ofSeconds(3);
    /** Shorter: the update runs inside Slack's own 3-second wait for the click's answer. */
    static final Duration UPDATE_TIMEOUT = Duration.ofMillis(1_500);

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final HttpClient http;
    private final URI api;
    private final Supplier<String> botToken;
    private final String channel;

    public SlackApprovalChannel(String apiUrl, String botToken, String channel) {
        this(apiUrl, constant(botToken), channel);
    }

    /** The token is asked for on every call (from the {@code SecretSource}: SSM on AWS, ADR 0018). */
    public SlackApprovalChannel(String apiUrl, Supplier<String> botToken, String channel) {
        if (channel == null || channel.isBlank()) {
            throw new IllegalArgumentException("A Slack bot token and channel are required");
        }
        this.http = HttpClient.newBuilder().connectTimeout(CONNECT_TIMEOUT).build();
        this.api = URI.create(apiUrl.replaceAll("/+$", "") + "/");
        this.botToken = botToken;
        this.channel = channel;
    }

    private static Supplier<String> constant(String botToken) {
        if (botToken == null || botToken.isBlank()) {
            throw new IllegalArgumentException("A Slack bot token and channel are required");
        }
        return () -> botToken;
    }

    @Override
    public ApprovalMessage post(ApprovalRequest request) {
        var reply = call("chat.postMessage", SlackApprovalMessage.body(channel, request).toString(), ANSWER_TIMEOUT);
        return new ApprovalMessage(reply.path("channel").asString(), reply.path("ts").asString());
    }

    /** {@code chat.update} on the bot's own message (scope chat:write). */
    @Override
    public void showOutcome(ApprovalMessage message, ApprovalRequest request, ApprovalOutcome outcome) {
        call("chat.update", SlackApprovalMessage.decided(message, request, outcome).toString(), UPDATE_TIMEOUT);
    }

    /** Calls a Web API method; returns the reply if {@code ok}, else throws. */
    private tools.jackson.databind.JsonNode call(String method, String body, Duration timeout) {
        String token;
        try {
            token = botToken.get();
        } catch (RuntimeException e) {
            throw new ApprovalChannelException("Slack bot token could not be read (" + e.getClass().getSimpleName() + ")");
        }
        if (token == null || token.isBlank()) {
            throw new ApprovalChannelException("Slack bot token is not set");
        }
        var httpRequest = HttpRequest.newBuilder(api.resolve(method))
                .timeout(timeout)
                .header("Authorization", "Bearer " + token)
                .header("Content-Type", "application/json; charset=utf-8")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> response;
        try {
            response = http.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        } catch (HttpTimeoutException e) {
            throw new ApprovalChannelException("Slack did not answer in time");
        } catch (IOException e) {
            throw new ApprovalChannelException("Slack is unreachable: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApprovalChannelException("Interrupted while posting to Slack");
        }

        if (response.statusCode() == 429) {
            var retryAfter = response.headers().firstValue("Retry-After").orElse("?");
            throw new ApprovalChannelException("Slack rate limit; retry after " + retryAfter + " s");
        }
        if (response.statusCode() != 200) {
            throw new ApprovalChannelException("Slack answered HTTP " + response.statusCode());
        }
        try {
            var reply = JSON.readTree(response.body());
            if (!reply.path("ok").asBoolean(false)) {
                throw new ApprovalChannelException("Slack refused the message: " + reply.path("error").asString("no error given"));
            }
            return reply;
        } catch (JacksonException e) {
            throw new ApprovalChannelException("Slack answered with a body that is not JSON");
        }
    }
}
