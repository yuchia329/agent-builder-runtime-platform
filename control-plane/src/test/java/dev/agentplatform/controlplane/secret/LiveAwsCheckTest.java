package dev.agentplatform.controlplane.secret;

import static org.assertj.core.api.Assertions.assertThat;

import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.tenant.Principals;
import dev.agentplatform.controlplane.token.KmsRunTokenSigner;
import dev.agentplatform.controlplane.token.RunTokenIssuer;
import java.time.Clock;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import software.amazon.awssdk.auth.credentials.ProfileCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.ssm.SsmClient;
import tools.jackson.databind.json.JsonMapper;

/**
 * Against the real dev environment (infra/envs/dev), only with {@code LIVE_AWS=1}: one KMS
 * signature (about $0.00003) and one SSM read. Uses the AWS profile {@code default} (never prod).
 * Prints no secret value: it only checks that each one is there.
 *
 * <pre>LIVE_AWS=1 ./gradlew test --tests '*LiveAwsCheckTest'</pre>
 */
@EnabledIfEnvironmentVariable(named = "LIVE_AWS", matches = "1")
class LiveAwsCheckTest {

    private static final String PROFILE = "default";
    private static final Region REGION = Region.US_EAST_1;

    @Test
    void kmsSignsATokenThatVerifiesWithItsPublicKey() throws Exception {
        try (var kms = KmsClient.builder().region(REGION)
                .credentialsProvider(ProfileCredentialsProvider.create(PROFILE)).build()) {
            var signer = new KmsRunTokenSigner(kms, "alias/agent-platform-dev-run-token");
            var loaded = new RunService.RunWithSpec(
                    Run.queued("run_live", "tenant-live", "issue-triage", 1, "{}", Principals.WEBHOOK_GITHUB,
                            Clock.systemUTC().instant()),
                    "{\"acts_as\":\"team-bot\",\"tools\":[\"github.add_label\"],\"budget\":{\"max_seconds\":60}}",
                    "api:tenant-live");

            var jwt = SignedJWT.parse(new RunTokenIssuer(signer, Clock.systemUTC()).issue(loaded));

            assertThat(jwt.verify(new ECDSAVerifier(signer.publicKey()))).isTrue();
            assertThat(jwt.getHeader().getKeyID()).isEqualTo(signer.keyId());
            var claims = JsonMapper.builder().build().readTree(jwt.getPayload().toString());
            assertThat(new ContractValidator().validate(ContractValidator.RUN_TOKEN, claims)).isEmpty();
        }
    }

    @Test
    void everySecretParameterCanBeRead() {
        try (var ssm = SsmClient.builder().region(REGION)
                .credentialsProvider(ProfileCredentialsProvider.create(PROFILE)).build()) {
            var source = new SsmSecretSource(ssm, "/agent-platform/dev/control-plane");

            for (var secret : Secret.values()) {
                assertThat(source.get(secret)).as(secret.name()).isNotBlank();
            }
        }
    }
}
