package dev.agentplatform.controlplane.secret;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;

/** What every {@link SecretSource} must do. Subclasses build a source holding the given values. */
abstract class SecretSourceContractTest {

    static final String API_KEY = "tenant-key-0123456789-0123456789-abcd";
    static final String WEBHOOK = "github-webhook-secret-value";

    /** A source whose stored values are these (others unset); the tenant API key is always among them. */
    abstract SecretSource source(Map<Secret, String> values);

    private SecretSource apiKeyAndWebhook() {
        return source(Map.of(Secret.TENANT_API_KEY, API_KEY, Secret.GITHUB_WEBHOOK_SECRET, WEBHOOK));
    }

    @Test
    void getReturnsEachStoredValue() {
        var source = apiKeyAndWebhook();

        assertThat(source.get(Secret.TENANT_API_KEY)).isEqualTo(API_KEY);
        assertThat(source.get(Secret.GITHUB_WEBHOOK_SECRET)).isEqualTo(WEBHOOK);
    }

    @Test
    void anUnsetSecretIsNull() {
        assertThat(apiKeyAndWebhook().get(Secret.SLACK_SIGNING_SECRET)).isNull();
    }

    @Test
    void storedSecretsCountAsConfigured() {
        var source = apiKeyAndWebhook();

        assertThat(source.configured(Secret.TENANT_API_KEY)).isTrue();
        assertThat(source.configured(Secret.GITHUB_WEBHOOK_SECRET)).isTrue();
    }

    @Test
    void peekIsNullOrTheValueAndHoldsItAfterGet() {
        var source = apiKeyAndWebhook();

        var before = source.peek(Secret.GITHUB_WEBHOOK_SECRET);
        assertThat(before == null || before.equals(WEBHOOK)).isTrue();
        source.get(Secret.TENANT_API_KEY);
        assertThat(source.peek(Secret.GITHUB_WEBHOOK_SECRET)).isEqualTo(WEBHOOK);
    }

    @Test
    void prefetchNeverBreaksALaterGet() {
        var source = apiKeyAndWebhook();

        source.prefetch();

        assertThat(source.get(Secret.TENANT_API_KEY)).isEqualTo(API_KEY);
    }
}
