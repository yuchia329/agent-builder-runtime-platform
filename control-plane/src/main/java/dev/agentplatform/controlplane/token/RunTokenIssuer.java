package dev.agentplatform.controlplane.token;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.ECDSASigner;
import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jose.jwk.Curve;
import com.nimbusds.jose.jwk.ECKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.run.RunService;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.time.Clock;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Date;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Signs run tokens (ADR 0003, contracts/run-token.schema.json): an ES256 JWT per
 * {@code GET /internal/runs/{id}}, with {@code kid} = the public key's RFC 7638 thumbprint (the
 * gateway computes the same from its public key), valid for the spec's {@code budget.max_seconds}.
 *
 * <p>Keys: {@code RUN_TOKEN_PRIVATE_KEY} (PKCS#8, secret) and {@code RUN_TOKEN_PUBLIC_KEY} (X.509
 * SubjectPublicKeyInfo), P-256, as PEM or the base64 body on one line. Both unset: no tokens (the
 * worker needs none until it calls the gateway, M4). One without the other, an unreadable key or a
 * pair that does not match fails startup. Keys and tokens are never logged.
 */
@Component
public class RunTokenIssuer {

    private static final Logger log = LoggerFactory.getLogger(RunTokenIssuer.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    static final String ISSUER = "control-plane";
    static final String AUDIENCE = "tool-gateway";

    private final ECKey key; // null when off
    private final Clock clock;

    @Autowired
    public RunTokenIssuer(PlatformProperties props, Clock clock) {
        this(props.runTokenPrivateKey(), props.runTokenPublicKey(), clock);
    }

    public RunTokenIssuer(String privateKeyPem, String publicKeyPem, Clock clock) {
        this.clock = clock;
        if (privateKeyPem == null && publicKeyPem == null) {
            this.key = null;
            log.info("Run tokens off: RUN_TOKEN_PRIVATE_KEY and RUN_TOKEN_PUBLIC_KEY not set");
            return;
        }
        if (privateKeyPem == null || publicKeyPem == null) {
            throw new IllegalStateException("Set both RUN_TOKEN_PRIVATE_KEY and RUN_TOKEN_PUBLIC_KEY, or neither");
        }
        this.key = keyPair(privateKeyPem, publicKeyPem);
        log.info("Run tokens on, key id {}", key.getKeyID());
    }

    public boolean enabled() {
        return key != null;
    }

    /** The key id tokens carry ({@code kid}); null when off. */
    public String keyId() {
        return key == null ? null : key.getKeyID();
    }

    /** A signed token for this run and its spec, or null when tokens are off. */
    public String issue(RunService.RunWithSpec loaded) {
        if (key == null) {
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
        var header = new JWSHeader.Builder(JWSAlgorithm.ES256).keyID(key.getKeyID()).type(JOSEObjectType.JWT).build();
        var jwt = new SignedJWT(header, claims);
        try {
            jwt.sign(new ECDSASigner(key));
        } catch (JOSEException e) {
            throw new IllegalStateException("Could not sign the run token of " + run.runId(), e);
        }
        return jwt.serialize();
    }

    private static List<String> strings(JsonNode array) {
        var values = new ArrayList<String>();
        array.forEach(item -> values.add(item.asString()));
        return values;
    }

    private static ECKey keyPair(String privatePem, String publicPem) {
        ECPrivateKey privateKey;
        ECPublicKey publicKey;
        try {
            var factory = KeyFactory.getInstance("EC");
            privateKey = (ECPrivateKey) factory.generatePrivate(new PKCS8EncodedKeySpec(der(privatePem)));
            publicKey = (ECPublicKey) factory.generatePublic(new X509EncodedKeySpec(der(publicPem)));
        } catch (GeneralSecurityException | IllegalArgumentException | ClassCastException e) {
            // Never include the key text in the message.
            throw new IllegalStateException("RUN_TOKEN_PRIVATE_KEY / RUN_TOKEN_PUBLIC_KEY is not a P-256 key in"
                    + " PKCS#8 / X.509 form (" + e.getClass().getSimpleName() + ")");
        }
        try {
            if (!Curve.forECParameterSpec(publicKey.getParams()).equals(Curve.P_256)) {
                throw new IllegalStateException("Run token keys must be on curve P-256 (ES256)");
            }
            var key = new ECKey.Builder(Curve.P_256, publicKey).privateKey(privateKey).keyIDFromThumbprint().build();
            // The pair must match: sign a probe with the private key, verify with the public one.
            var probe = new SignedJWT(new JWSHeader(JWSAlgorithm.ES256), new JWTClaimsSet.Builder().build());
            probe.sign(new ECDSASigner(key));
            if (!probe.verify(new ECDSAVerifier(publicKey))) {
                throw new IllegalStateException("RUN_TOKEN_PUBLIC_KEY does not match RUN_TOKEN_PRIVATE_KEY");
            }
            return key;
        } catch (JOSEException e) {
            throw new IllegalStateException("Run token keys could not be used (" + e.getClass().getSimpleName() + ")");
        }
    }

    /** PEM (any header) or a bare base64 body; literal "\n" (from .env files) counts as a line break. */
    static byte[] der(String pem) {
        var body = pem.replace("\\n", "\n").replaceAll("-----(BEGIN|END) [A-Z ]+-----", "").replaceAll("\\s", "");
        return Base64.getDecoder().decode(body);
    }
}
