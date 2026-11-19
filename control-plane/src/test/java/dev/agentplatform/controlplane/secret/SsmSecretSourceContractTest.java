package dev.agentplatform.controlplane.secret;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import java.time.Duration;
import java.util.Map;
import org.junit.jupiter.api.Test;

class SsmSecretSourceContractTest extends SecretSourceContractTest {

    private static final String PATH = "/agent-platform/test/control-plane";

    private final FakeSsm ssm = new FakeSsm();

    @Override
    SecretSource source(Map<Secret, String> values) {
        values.forEach((secret, value) -> ssm.put(PATH + "/" + secret.parameter(), value));
        return new SsmSecretSource(ssm, PATH);
    }

    @Test
    void nothingIsReadAtConstruction() {
        source(Map.of(Secret.TENANT_API_KEY, API_KEY));

        assertThat(ssm.calls).hasValue(0);
    }

    @Test
    void oneCallWithDecryptionLoadsEverySecretOnce() {
        var source = source(Map.of(Secret.TENANT_API_KEY, API_KEY, Secret.GITHUB_WEBHOOK_SECRET, WEBHOOK));

        for (var secret : Secret.values()) {
            source.get(secret);
            source.get(secret);
        }

        assertThat(ssm.calls).hasValue(1);
        assertThat(ssm.requests.getFirst().withDecryption()).isTrue();
        assertThat(ssm.requests.getFirst().names()).containsExactlyInAnyOrder(
                PATH + "/tenant-api-key", PATH + "/github-webhook-secret",
                PATH + "/slack-bot-token", PATH + "/slack-signing-secret");
    }

    @Test
    void peekNeverLoads() {
        var source = source(Map.of(Secret.TENANT_API_KEY, API_KEY));

        assertThat(source.peek(Secret.TENANT_API_KEY)).isNull();
        assertThat(ssm.calls).hasValue(0);
    }

    @Test
    void everySecretCountsAsConfiguredWithoutLoading() {
        var source = source(Map.of(Secret.TENANT_API_KEY, API_KEY));

        assertThat(source.configured(Secret.SLACK_BOT_TOKEN)).isTrue();
        assertThat(ssm.calls).hasValue(0);
    }

    @Test
    void aTrailingSlashInThePathIsIgnored() {
        ssm.put(PATH + "/tenant-api-key", API_KEY);

        assertThat(new SsmSecretSource(ssm, PATH + "/").get(Secret.TENANT_API_KEY)).isEqualTo(API_KEY);
    }

    @Test
    void prefetchLoadsInTheBackground() {
        var source = source(Map.of(Secret.TENANT_API_KEY, API_KEY));

        source.prefetch();

        await().atMost(Duration.ofSeconds(5)).until(() -> source.peek(Secret.TENANT_API_KEY) != null);
        assertThat(ssm.calls).hasValue(1);
    }

    @Test
    void aFailedLoadIsNotKeptAndNamesNoValue() {
        var source = source(Map.of(Secret.TENANT_API_KEY, API_KEY));
        ssm.failing = true;

        assertThatThrownBy(() -> source.get(Secret.TENANT_API_KEY))
                .isInstanceOf(SecretUnavailableException.class)
                .hasMessageContaining(PATH)
                .hasMessageNotContaining(API_KEY);

        ssm.failing = false;
        assertThat(source.get(Secret.TENANT_API_KEY)).isEqualTo(API_KEY);
        assertThat(ssm.calls).hasValue(2);
    }

    @Test
    void aPathThatIsNotAnSsmPathFailsStartup() {
        assertThatThrownBy(() -> new SsmSecretSource(ssm, "agent-platform/dev"))
                .hasMessageContaining("SECRETS_SSM_PATH");
        assertThatThrownBy(() -> new SsmSecretSource(ssm, null))
                .hasMessageContaining("SECRETS_SSM_PATH");
    }
}
