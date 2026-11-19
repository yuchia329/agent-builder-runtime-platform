package dev.agentplatform.controlplane.token;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jose.jwk.Curve;
import com.nimbusds.jose.jwk.ECKey;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.tenant.Principals;
import java.security.interfaces.ECPublicKey;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class RunTokenIssuerTest {

    private static final Instant NOW = Instant.parse("2026-11-19T10:00:00Z");
    private static final String SPEC = """
            {"name":"issue-triage","acts_as":"team-bot",
             "tools":["github.search_issues","github.add_label","github.close_issue"],
             "require_approval":["github.close_issue"],
             "budget":{"max_steps":20,"max_usd":0.05,"max_seconds":180}}""";
    private static final RunService.RunWithSpec LOADED = new RunService.RunWithSpec(
            Run.queued("run_abc", "tenant-demo", "issue-triage", 4, "{}", Principals.WEBHOOK_GITHUB, NOW),
            SPEC, "api:tenant-demo");

    private final TestKeys keys = TestKeys.p256();
    private final Clock clock = Clock.fixed(NOW, ZoneOffset.UTC);

    private RunTokenIssuer issuer() {
        return new RunTokenIssuer(keys.privatePem, keys.publicPem, clock);
    }

    @Test
    void tokenIsSignedWithES256AndTheKeyThumbprint() throws Exception {
        var jwt = SignedJWT.parse(issuer().issue(LOADED));

        assertThat(jwt.getHeader().getAlgorithm()).isEqualTo(JWSAlgorithm.ES256);
        assertThat(jwt.verify(new ECDSAVerifier((ECPublicKey) keys.pair.getPublic()))).isTrue();
        var thumbprint = new ECKey.Builder(Curve.P_256, (ECPublicKey) keys.pair.getPublic()).build()
                .computeThumbprint().toString();
        assertThat(jwt.getHeader().getKeyID()).isEqualTo(thumbprint).isEqualTo(issuer().keyId());
    }

    @Test
    void claimsFollowTheContract() throws Exception {
        var claims = SignedJWT.parse(issuer().issue(LOADED)).getPayload().toString();

        var json = JsonMapper.builder().build().readTree(claims);
        assertThat(new ContractValidator().validate(ContractValidator.RUN_TOKEN, json)).isEmpty();
        assertThat(json.get("sub").asString()).isEqualTo("run_abc");
        assertThat(json.get("aud").asString()).isEqualTo("tool-gateway");
        assertThat(json.get("owner").asString()).isEqualTo("api:tenant-demo");
        assertThat(json.get("started_by").asString()).isEqualTo("webhook:github");
        assertThat(json.get("acts_as").asString()).isEqualTo("team-bot");
        assertThat(json.get("agent_version").asInt()).isEqualTo(4);
        assertThat(json.get("tools")).hasSize(3);
        assertThat(json.get("require_approval").get(0).asString()).isEqualTo("github.close_issue");
    }

    @Test
    void tokenLastsTheSpecsMaxSecondsFromIssue() throws Exception {
        var claims = SignedJWT.parse(issuer().issue(LOADED)).getJWTClaimsSet();

        assertThat(claims.getIssueTime().toInstant()).isEqualTo(NOW);
        assertThat(claims.getExpirationTime().toInstant()).isEqualTo(NOW.plusSeconds(180));
    }

    @Test
    void specWithoutRequireApprovalGivesAnEmptyList() throws Exception {
        var loaded = new RunService.RunWithSpec(LOADED.run(), SPEC.replace(
                "\"require_approval\":[\"github.close_issue\"],", ""), "api:tenant-demo");

        var claims = SignedJWT.parse(issuer().issue(loaded)).getJWTClaimsSet();

        assertThat(claims.getStringListClaim("require_approval")).isEmpty();
    }

    @Test
    void eachIssueIsANewToken() {
        var later = new RunTokenIssuer(keys.privatePem, keys.publicPem, Clock.fixed(NOW.plusSeconds(1), ZoneOffset.UTC));

        assertThat(later.issue(LOADED)).isNotEqualTo(issuer().issue(LOADED));
    }

    @Test
    void withoutKeysTokensAreOff() {
        var off = new RunTokenIssuer((String) null, null, clock);

        assertThat(off.enabled()).isFalse();
        assertThat(off.issue(LOADED)).isNull();
    }

    @Test
    void oneLineBodiesAndEscapedNewlinesAreAccepted() {
        var oneLine = keys.privatePem.replaceAll("-----[A-Z ]+-----", "").replace("\n", "");
        var escaped = keys.publicPem.replace("\n", "\\n");

        assertThat(new RunTokenIssuer(oneLine, escaped, clock).keyId()).isEqualTo(issuer().keyId());
    }

    @Test
    void halfConfiguredKeysFailStartup() {
        assertThatThrownBy(() -> new RunTokenIssuer(keys.privatePem, null, clock))
                .hasMessageContaining("both");
        assertThatThrownBy(() -> new RunTokenIssuer(null, keys.publicPem, clock))
                .hasMessageContaining("both");
    }

    @Test
    void mismatchedPairFailsStartup() {
        assertThatThrownBy(() -> new RunTokenIssuer(keys.privatePem, TestKeys.p256().publicPem, clock))
                .hasMessageContaining("does not match");
    }

    @Test
    void otherCurveFailsStartup() {
        var p384 = TestKeys.p384();

        assertThatThrownBy(() -> new RunTokenIssuer(p384.privatePem, p384.publicPem, clock))
                .hasMessageContaining("P-256");
    }

    @Test
    void unreadableKeyFailsWithoutShowingIt() {
        var broken = keys.privatePem.replace("M", "*");

        assertThatThrownBy(() -> new RunTokenIssuer(broken, keys.publicPem, clock))
                .isInstanceOf(IllegalStateException.class)
                .satisfies(e -> assertThat(e.getMessage()).doesNotContain(keys.privatePem.substring(30, 60)));
    }
}
