package dev.agentplatform.controlplane.token;

import static org.assertj.core.api.Assertions.assertThat;

import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jose.jwk.Curve;
import com.nimbusds.jose.jwk.ECKey;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.tenant.Principals;
import java.nio.charset.StandardCharsets;
import java.security.interfaces.ECPublicKey;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/**
 * What every {@link RunTokenSigner} must do: ES256 signatures in JOSE form that verify with its
 * public key, a key id the gateway can compute from that key, and tokens that follow the contract.
 */
abstract class RunTokenSignerContractTest {

    static final Instant NOW = Instant.parse("2026-11-19T10:00:00Z");
    static final RunService.RunWithSpec LOADED = new RunService.RunWithSpec(
            Run.queued("run_abc", "tenant-demo", "issue-triage", 4, "{}", Principals.WEBHOOK_GITHUB, NOW),
            """
            {"name":"issue-triage","acts_as":"team-bot",
             "tools":["github.search_issues","github.add_label","github.close_issue"],
             "require_approval":["github.close_issue"],
             "budget":{"max_steps":20,"max_usd":0.05,"max_seconds":180}}""",
            "api:tenant-demo");

    /** A signer and the public key it should verify with, known independently of the signer. */
    abstract RunTokenSigner signer();

    abstract ECPublicKey expectedPublicKey();

    @Test
    void signaturesAreJoseFormAndVerifyWithThePublicKey() {
        var signer = signer();
        var input = "header.payload".getBytes(StandardCharsets.US_ASCII);

        var signature = signer.sign(input);

        assertThat(signature).hasSize(64);
        assertThat(Es256.verify(expectedPublicKey(), input, signature)).isTrue();
        assertThat(Es256.verify(expectedPublicKey(), "header.other".getBytes(StandardCharsets.US_ASCII), signature))
                .isFalse();
    }

    @Test
    void publicKeyAndKeyIdMatchTheKey() throws Exception {
        var signer = signer();
        var thumbprint = new ECKey.Builder(Curve.P_256, expectedPublicKey()).build().computeThumbprint().toString();

        assertThat(signer.publicKey().getEncoded()).isEqualTo(expectedPublicKey().getEncoded());
        assertThat(signer.keyId()).isEqualTo(thumbprint);
    }

    @Test
    void issuedTokensVerifyAndFollowTheContract() throws Exception {
        var signer = signer();
        var issuer = new RunTokenIssuer(signer, Clock.fixed(NOW, ZoneOffset.UTC));

        var jwt = SignedJWT.parse(issuer.issue(LOADED));

        assertThat(jwt.verify(new ECDSAVerifier(expectedPublicKey()))).isTrue();
        assertThat(jwt.getHeader().getKeyID()).isEqualTo(signer.keyId());
        var claims = JsonMapper.builder().build().readTree(jwt.getPayload().toString());
        assertThat(new ContractValidator().validate(ContractValidator.RUN_TOKEN, claims)).isEmpty();
    }
}
