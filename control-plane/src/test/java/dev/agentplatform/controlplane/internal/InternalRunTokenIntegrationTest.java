package dev.agentplatform.controlplane.internal;

import static org.assertj.core.api.Assertions.assertThat;

import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.token.TestKeys;
import java.security.interfaces.ECPublicKey;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.json.JsonMapper;

/** GET /internal/runs/{id} with run token keys configured: a verifiable token per request. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class InternalRunTokenIntegrationTest {

    private static final TestKeys KEYS = TestKeys.p256();

    @DynamicPropertySource
    static void keys(DynamicPropertyRegistry registry) {
        registry.add("platform.run-token.private-key", () -> KEYS.privatePem);
        registry.add("platform.run-token.public-key", () -> KEYS.publicPem);
    }

    private final ContractValidator contracts = new ContractValidator();

    @Value("${local.server.port}")
    int port;

    @Test
    void getCarriesAVerifiableTokenForThatRun() throws Exception {
        var api = new ApiClient(port);
        api.publishExampleAgent("token-get");
        var started = api.postJson("/runs", "{\"agent\":\"token-get\",\"input\":{}}");
        var runId = ApiClient.json(started).get("run_id").asString();

        var run = ApiClient.json(api.send("GET", "/internal/runs/" + runId, null, null, false));

        assertThat(contracts.validate(ContractValidator.INTERNAL_RUN, run)).isEmpty();
        var jwt = SignedJWT.parse(run.get("run_token").asString());
        assertThat(jwt.verify(new ECDSAVerifier((ECPublicKey) KEYS.pair.getPublic()))).isTrue();
        var claims = JsonMapper.builder().build().readTree(jwt.getPayload().toString());
        assertThat(contracts.validate(ContractValidator.RUN_TOKEN, claims)).isEmpty();
        assertThat(claims.get("sub").asString()).isEqualTo(runId);
        assertThat(claims.get("tenant").asString()).isEqualTo("tenant-test");
        assertThat(claims.get("owner").asString()).isEqualTo("api:tenant-test");
        assertThat(claims.get("started_by").asString()).isEqualTo("api:tenant-test");
        assertThat(claims.get("tools")).isEqualTo(run.get("spec").get("tools"));
        assertThat(claims.get("exp").asLong() - claims.get("iat").asLong())
                .isEqualTo(run.get("spec").get("budget").get("max_seconds").asLong());
        // The public API never shows the token.
        assertThat(api.send("GET", "/runs/" + runId, null, null, true).body()).doesNotContain("run_token");
    }
}
