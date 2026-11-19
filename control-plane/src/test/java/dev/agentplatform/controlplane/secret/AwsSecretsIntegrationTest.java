package dev.agentplatform.controlplane.secret;

import static org.assertj.core.api.Assertions.assertThat;

import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.token.FakeKms;
import dev.agentplatform.controlplane.trigger.GitHubWebhooks;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.interfaces.ECPublicKey;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.ssm.SsmClient;
import tools.jackson.databind.json.JsonMapper;

/**
 * The app configured as on AWS (ADR 0018): secrets from SSM ({@code SECRETS_SOURCE=ssm}, no
 * TENANT_API_KEY in the environment) and run tokens signed by KMS, against in-memory fakes.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=",
        "platform.github.webhook-secret=",
        "platform.secrets.source=ssm",
        "platform.secrets.ssm-path=" + AwsSecretsIntegrationTest.PATH,
        "platform.run-token.kms-key-id=" + FakeKms.ALIAS,
        "platform.slack.approval-channel=C0123456789",
        "platform.slack.api-url=http://127.0.0.1:9"})
class AwsSecretsIntegrationTest {

    static final String PATH = "/agent-platform/test/control-plane";
    private static final String WEBHOOK_SECRET = "webhook-secret-from-ssm";
    private static final FakeSsm SSM = new FakeSsm()
            .put(PATH + "/tenant-api-key", ApiClient.API_KEY)
            .put(PATH + "/github-webhook-secret", WEBHOOK_SECRET)
            .put(PATH + "/slack-bot-token", "xoxb-test")
            .put(PATH + "/slack-signing-secret", "slack-signing-secret");
    private static final FakeKms KMS = new FakeKms();

    @TestConfiguration
    static class Fakes {
        @Bean
        @Primary
        SsmClient fakeSsm() {
            return SSM;
        }

        @Bean
        @Primary
        KmsClient fakeKms() {
            return KMS;
        }
    }

    @Value("${local.server.port}")
    int port;

    @Autowired
    SecretSource secrets;

    @Test
    void theApiKeyComesFromSsmAndIsReadOnce() throws Exception {
        var api = new ApiClient(port);

        assertThat(api.send("POST", "/agents", "{\"name\":\"ssm-key\"}", "application/json", true).statusCode())
                .isEqualTo(201);
        assertThat(api.send("POST", "/agents", "{\"name\":\"ssm-other\"}", "application/json", false).statusCode())
                .isEqualTo(401);
        assertThat(SSM.calls).hasValue(1);
        assertThat(secrets).isInstanceOf(SsmSecretSource.class);
    }

    @Test
    void webhooksAreVerifiedWithTheSsmSecret() throws Exception {
        var body = "{\"zen\":\"hi\"}".getBytes(StandardCharsets.UTF_8);
        var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/triggers/github"))
                .header("Content-Type", "application/json")
                .header("X-GitHub-Event", "ping")
                .header("X-GitHub-Delivery", "ssm-ping")
                .header("X-Hub-Signature-256", GitHubWebhooks.sign(WEBHOOK_SECRET, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();

        var response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());

        assertThat(response.statusCode()).isEqualTo(200);
    }

    @Test
    void runTokensAreSignedByKms() throws Exception {
        var api = new ApiClient(port);
        api.publishExampleAgent("kms-token");
        var runId = ApiClient.json(api.postJson("/runs", "{\"agent\":\"kms-token\",\"input\":{}}"))
                .get("run_id").asString();

        var run = ApiClient.json(api.send("GET", "/internal/runs/" + runId, null, null, false));

        var jwt = SignedJWT.parse(run.get("run_token").asString());
        assertThat(jwt.verify(new ECDSAVerifier((ECPublicKey) KMS.pair().getPublic()))).isTrue();
        var claims = JsonMapper.builder().build().readTree(jwt.getPayload().toString());
        assertThat(new ContractValidator().validate(ContractValidator.RUN_TOKEN, claims)).isEmpty();
        assertThat(claims.get("sub").asString()).isEqualTo(runId);
        assertThat(KMS.signCalls.get()).isPositive();
    }
}
