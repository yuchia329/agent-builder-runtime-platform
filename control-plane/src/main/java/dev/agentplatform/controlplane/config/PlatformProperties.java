package dev.agentplatform.controlplane.config;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/** Settings under {@code platform.*}; values come from environment variables (see application.yml). */
@Validated
@ConfigurationProperties("platform")
public record PlatformProperties(
        @NotNull StoreBackend storeBackend,
        @NotBlank String table,
        @NotNull QueueBackend queueBackend,
        String runQueueUrl,
        @Valid @NotNull TenantProperties tenant,
        GitHubProperties github,
        SweepProperties sweep,
        SlackProperties slack) {

    public enum StoreBackend { MEMORY, DYNAMODB }

    public enum QueueBackend { MEMORY, SQS }

    /** The single hard-coded tenant used until tenants are stored (M0). */
    public record TenantProperties(@NotBlank String id, @NotBlank String apiKey) {}

    /** {@code webhookSecret} signs GitHub webhooks; blank means the webhook route is off. */
    public record GitHubProperties(String webhookSecret) {}

    /**
     * Re-enqueue sweep: every {@code intervalSeconds} (0 = off), re-send runs QUEUED for longer
     * than {@code queuedAfterSeconds}.
     */
    public record SweepProperties(Integer intervalSeconds, Integer queuedAfterSeconds) {}

    /**
     * Slack bot for approval requests: {@code botToken} (scope chat:write; blank means approvals
     * are only logged), {@code approvalChannel} (channel id), {@code apiUrl} (tests point it at a
     * fake Slack), {@code signingSecret} (verifies button clicks).
     */
    public record SlackProperties(String botToken, String approvalChannel, String apiUrl, String signingSecret) {}

    /** Verifies {@code POST /approvals/slack}; blank means the route answers 503. */
    public String slackSigningSecret() {
        return slack == null ? null : blankToNull(slack.signingSecret());
    }

    public String slackBotToken() {
        return slack == null ? null : blankToNull(slack.botToken());
    }

    public String slackApprovalChannel() {
        return slack == null ? null : blankToNull(slack.approvalChannel());
    }

    public String slackApiUrl() {
        var url = slack == null ? null : blankToNull(slack.apiUrl());
        return url == null ? "https://slack.com/api" : url;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    public int sweepQueuedAfterSeconds() {
        return sweep == null || sweep.queuedAfterSeconds() == null ? 300 : sweep.queuedAfterSeconds();
    }

    public String githubWebhookSecret() {
        return github == null || github.webhookSecret() == null || github.webhookSecret().isBlank()
                ? null
                : github.webhookSecret();
    }
}
