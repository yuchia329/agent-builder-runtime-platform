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
        GitHubProperties github) {

    public enum StoreBackend { MEMORY, DYNAMODB }

    public enum QueueBackend { MEMORY, SQS }

    /** The single hard-coded tenant used until tenants are stored (M0). */
    public record TenantProperties(@NotBlank String id, @NotBlank String apiKey) {}

    /** {@code webhookSecret} signs GitHub webhooks; blank means the webhook route is off. */
    public record GitHubProperties(String webhookSecret) {}

    public String githubWebhookSecret() {
        return github == null || github.webhookSecret() == null || github.webhookSecret().isBlank()
                ? null
                : github.webhookSecret();
    }
}
