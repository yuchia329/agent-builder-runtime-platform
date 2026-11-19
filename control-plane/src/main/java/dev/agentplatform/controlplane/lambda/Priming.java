package dev.agentplatform.controlplane.lambda;

import dev.agentplatform.controlplane.approval.SlackInteractions;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.trigger.GitHubWebhooks;
import com.amazonaws.serverless.proxy.internal.LambdaContainerHandler;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Requests sent through the app before the SnapStart snapshot, so the snapshot holds the classes
 * (filters, controllers, Jackson, HMAC, problem responses) a first real request needs. Every one
 * stops before a store, queue or Slack call: nothing opens a connection that a restore would find
 * dead, and nothing is written. Signed with the secrets the {@link SecretSource} already holds
 * (env: the request then goes one step further); with SSM secrets nothing is loaded before the
 * snapshot (ADR 0018), so the requests go unsigned and stop at 401.
 */
final class Priming {

    private static final Logger log = LoggerFactory.getLogger(Priming.class);

    private Priming() {}

    /** Sends the priming requests; returns their status codes in order (logged too). */
    static List<Integer> run(LambdaHandler handler) {
        var secrets = handler.bean(SecretSource.class);
        var started = System.nanoTime();
        var statuses = events(secrets).stream().map(event -> status(send(handler, event))).toList();
        log.info("Priming done in {} ms, statuses {}", (System.nanoTime() - started) / 1_000_000, statuses);
        return statuses;
    }

    private static List<byte[]> events(SecretSource secrets) {
        var now = Long.toString(Instant.now().getEpochSecond());

        // Slack: a verified interaction that is not a click answers 200 before any store call.
        var slackBody = "payload=%7B%22type%22%3A%22view_closed%22%7D";
        var slackSecret = secrets.peek(Secret.SLACK_SIGNING_SECRET);
        var slackSignature = slackSecret == null ? "v0=00"
                : SlackInteractions.sign(slackSecret, now, slackBody.getBytes(StandardCharsets.UTF_8));
        var slack = HttpEvents.request("POST", "/approvals/slack", Map.of(
                "Content-Type", "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp", now,
                "X-Slack-Signature", slackSignature), slackBody);

        // GitHub: ping answers 200 before the delivery claim.
        var pingBody = "{\"zen\":\"priming\",\"hook_id\":0}";
        var githubSecret = secrets.peek(Secret.GITHUB_WEBHOOK_SECRET);
        var githubSignature = githubSecret == null ? "sha256=00"
                : GitHubWebhooks.sign(githubSecret, pingBody.getBytes(StandardCharsets.UTF_8));
        var github = HttpEvents.request("POST", "/triggers/github", Map.of(
                "Content-Type", "application/json",
                "X-GitHub-Event", "ping",
                "X-Hub-Signature-256", githubSignature), pingBody);

        // Public API: an invalid body is rejected (400) before the store; no key gets 401.
        var apiKey = secrets.peek(Secret.TENANT_API_KEY);
        var badRun = HttpEvents.request("POST", "/runs", Map.of(
                "Content-Type", "application/json",
                "X-Api-Key", apiKey == null ? "priming" : apiKey), "{\"agent\":\"priming\",\"input\":[]}");
        var noKey = HttpEvents.request("GET", "/runs/priming", Map.of(), null);

        return List.of(slack, github, badRun, noKey);
    }

    private static byte[] send(LambdaHandler handler, byte[] event) {
        var out = new ByteArrayOutputStream();
        try {
            handler.handleRequest(new ByteArrayInputStream(event), out, new LocalContext());
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return out.toByteArray();
    }

    static int status(byte[] response) {
        return LambdaContainerHandler.getObjectMapper().readTree(response).path("statusCode").asInt();
    }
}
