package dev.agentplatform.controlplane.config;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.time.Duration;
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
        SlackProperties slack,
        @Valid ApprovalProperties approval,
        RunTokenProperties runToken,
        SecretsProperties secrets) {

    public enum StoreBackend { MEMORY, DYNAMODB }

    public enum QueueBackend { MEMORY, SQS }

    /**
     * The single hard-coded tenant used until tenants are stored (M0). {@code apiKey} is
     * {@code TENANT_API_KEY}; read it through {@code SecretSource}, which requires it locally.
     */
    public record TenantProperties(@NotBlank String id, String apiKey) {}

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

    /**
     * A run left WAITING_APPROVAL longer than {@code timeoutHours} fails with
     * {@code approval_expired}. Must stay under the worker's 15-day (360 h) run-state retention
     * (ADR 0011), or an approved run would find its saved steps gone.
     */
    public record ApprovalProperties(@Min(1) @Max(359) Integer timeoutHours) {}

    /**
     * Run token signing: {@code kmsKeyId} (KMS key id, ARN or alias; AWS) or the local pair
     * {@code privateKey} / {@code publicKey} (P-256, PEM or base64 body); none means no tokens.
     */
    public record RunTokenProperties(String privateKey, String publicKey, String kmsKeyId) {}

    public enum SecretsSource { ENV, SSM }

    /**
     * Where secrets come from (ADR 0018): {@code source} env (default; the values above) or ssm
     * (parameters under {@code ssmPath}, read on first use).
     */
    public record SecretsProperties(SecretsSource source, String ssmPath) {}

    public SecretsSource secretsSource() {
        return secrets == null || secrets.source() == null ? SecretsSource.ENV : secrets.source();
    }

    public String secretsSsmPath() {
        return secrets == null ? null : blankToNull(secrets.ssmPath());
    }

    public String runTokenKmsKeyId() {
        return runToken == null ? null : blankToNull(runToken.kmsKeyId());
    }

    public String runTokenPrivateKey() {
        return runToken == null ? null : blankToNull(runToken.privateKey());
    }

    public String runTokenPublicKey() {
        return runToken == null ? null : blankToNull(runToken.publicKey());
    }

    public Duration approvalTimeout() {
        return Duration.ofHours(approval == null || approval.timeoutHours() == null ? 72 : approval.timeoutHours());
    }

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
