package dev.agentplatform.controlplane.token;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.util.Base64URL;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.run.RunService;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.services.kms.KmsClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Issues run tokens (ADR 0003, contracts/run-token.schema.json): an ES256 JWT per
 * {@code GET /internal/runs/{id}}, with {@code kid} = the public key's RFC 7638 thumbprint (the
 * gateway computes the same from its public key), valid for the spec's {@code budget.max_seconds}.
 *
 * <p>The {@link RunTokenSigner} comes from configuration: {@code RUN_TOKEN_KMS_KEY_ID} signs with
 * KMS (AWS, ADR 0018); {@code RUN_TOKEN_PRIVATE_KEY} with {@code RUN_TOKEN_PUBLIC_KEY} signs with
 * that pair (local); neither means no tokens. Both kinds, or half a pair, fail startup. Keys and
 * tokens are never logged.
 */
@Component
public class RunTokenIssuer {

    private static final Logger log = LoggerFactory.getLogger(RunTokenIssuer.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    static final String ISSUER = "control-plane";
    static final String AUDIENCE = "tool-gateway";

    private final RunTokenSigner signer; // null when off
    private final Clock clock;

    @Autowired
    public RunTokenIssuer(PlatformProperties props, ObjectProvider<KmsClient> kms, Clock clock) {
        this(signer(props, kms), clock);
    }

    /** Local key pair (PEM or base64 body); both null means off. */
    public RunTokenIssuer(String privateKeyPem, String publicKeyPem, Clock clock) {
        this(localSigner(privateKeyPem, publicKeyPem), clock);
    }

    public RunTokenIssuer(RunTokenSigner signer, Clock clock) {
        this.signer = signer;
        this.clock = clock;
    }

    private static RunTokenSigner signer(PlatformProperties props, ObjectProvider<KmsClient> kms) {
        var kmsKey = props.runTokenKmsKeyId();
        if (kmsKey != null) {
            if (props.runTokenPrivateKey() != null || props.runTokenPublicKey() != null) {
                throw new IllegalStateException(
                        "Set RUN_TOKEN_KMS_KEY_ID or the RUN_TOKEN_PRIVATE_KEY / RUN_TOKEN_PUBLIC_KEY pair, not both");
            }
            log.info("Run tokens on, signed by KMS key {} (public key read on first use)", kmsKey);
            return new KmsRunTokenSigner(kms.getObject(), kmsKey);
        }
        return localSigner(props.runTokenPrivateKey(), props.runTokenPublicKey());
    }

    private static RunTokenSigner localSigner(String privateKeyPem, String publicKeyPem) {
        if (privateKeyPem == null && publicKeyPem == null) {
            log.info("Run tokens off: no RUN_TOKEN_KMS_KEY_ID and no RUN_TOKEN_PRIVATE_KEY / RUN_TOKEN_PUBLIC_KEY");
            return null;
        }
        if (privateKeyPem == null || publicKeyPem == null) {
            throw new IllegalStateException("Set both RUN_TOKEN_PRIVATE_KEY and RUN_TOKEN_PUBLIC_KEY, or neither");
        }
        var signer = new LocalKeySigner(privateKeyPem, publicKeyPem);
        log.info("Run tokens on, key id {}", signer.keyId());
        return signer;
    }

    public boolean enabled() {
        return signer != null;
    }

    /** The key id tokens carry ({@code kid}); null when off. May call KMS on first use. */
    public String keyId() {
        return signer == null ? null : signer.keyId();
    }

    /** A signed token for this run and its spec, or null when tokens are off. */
    public String issue(RunService.RunWithSpec loaded) {
        if (signer == null) {
            return null;
        }
        var run = loaded.run();
        var spec = JSON.readTree(loaded.specJson());
        var now = clock.instant().getEpochSecond();
        var claims = new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(AUDIENCE)
                .subject(run.runId())
                .claim("tenant", run.tenantId())
                .claim("agent", run.agentName())
                .claim("agent_version", run.agentVersion())
                .claim("acts_as", spec.path("acts_as").asString())
                .claim("owner", loaded.owner())
                .claim("started_by", run.startedBy())
                .claim("tools", strings(spec.path("tools")))
                .claim("require_approval", strings(spec.path("require_approval")))
                .issueTime(new Date(now * 1000))
                .expirationTime(new Date((now + spec.path("budget").path("max_seconds").asLong()) * 1000))
                .build();
        var header = new JWSHeader.Builder(JWSAlgorithm.ES256).keyID(signer.keyId()).type(JOSEObjectType.JWT).build();
        var signingInput = new SignedJWT(header, claims).getSigningInput();
        var signature = Base64URL.encode(signer.sign(signingInput));
        return new String(signingInput, StandardCharsets.US_ASCII) + "." + signature;
    }

    private static List<String> strings(JsonNode array) {
        var values = new ArrayList<String>();
        array.forEach(item -> values.add(item.asString()));
        return values;
    }
}
