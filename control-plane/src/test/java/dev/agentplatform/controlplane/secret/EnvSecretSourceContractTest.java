package dev.agentplatform.controlplane.secret;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.EnumMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class EnvSecretSourceContractTest extends SecretSourceContractTest {

    @Override
    SecretSource source(Map<Secret, String> values) {
        return new EnvSecretSource(values);
    }

    @Test
    void blankCountsAsUnsetAndNotConfigured() {
        var values = new EnumMap<Secret, String>(Map.of(Secret.TENANT_API_KEY, API_KEY));
        values.put(Secret.SLACK_BOT_TOKEN, " ");
        values.put(Secret.SLACK_SIGNING_SECRET, null);
        var source = new EnvSecretSource(values);

        assertThat(source.get(Secret.SLACK_BOT_TOKEN)).isNull();
        assertThat(source.configured(Secret.SLACK_BOT_TOKEN)).isFalse();
        assertThat(source.configured(Secret.SLACK_SIGNING_SECRET)).isFalse();
    }

    @Test
    void withoutTheTenantApiKeyStartupFails() {
        assertThatThrownBy(() -> new EnvSecretSource(Map.of(Secret.GITHUB_WEBHOOK_SECRET, WEBHOOK)))
                .hasMessageContaining("TENANT_API_KEY");
    }
}
