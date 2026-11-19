package dev.agentplatform.controlplane.token;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.crypto.impl.ECDSA;
import com.nimbusds.jose.jwk.Curve;
import com.nimbusds.jose.jwk.ECKey;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.Signature;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;

/**
 * Signs with a key pair from configuration: {@code RUN_TOKEN_PRIVATE_KEY} (PKCS#8, secret) and
 * {@code RUN_TOKEN_PUBLIC_KEY} (X.509 SubjectPublicKeyInfo), P-256, as PEM or the base64 body on
 * one line. An unreadable key or a pair that does not match fails at construction (startup). Key
 * text never appears in an error.
 */
public class LocalKeySigner implements RunTokenSigner {

    private final ECPrivateKey privateKey;
    private final ECPublicKey publicKey;
    private final String keyId;

    public LocalKeySigner(String privateKeyPem, String publicKeyPem) {
        try {
            var factory = KeyFactory.getInstance("EC");
            privateKey = (ECPrivateKey) factory.generatePrivate(new PKCS8EncodedKeySpec(der(privateKeyPem)));
            publicKey = (ECPublicKey) factory.generatePublic(new X509EncodedKeySpec(der(publicKeyPem)));
        } catch (GeneralSecurityException | IllegalArgumentException | ClassCastException e) {
            throw new IllegalStateException("RUN_TOKEN_PRIVATE_KEY / RUN_TOKEN_PUBLIC_KEY is not a P-256 key in"
                    + " PKCS#8 / X.509 form (" + e.getClass().getSimpleName() + ")");
        }
        keyId = thumbprint(publicKey);
        // The pair must match: sign a probe with the private key, verify with the public one.
        var probe = new byte[] {1, 2, 3};
        if (!Es256.verify(publicKey, probe, sign(probe))) {
            throw new IllegalStateException("RUN_TOKEN_PUBLIC_KEY does not match RUN_TOKEN_PRIVATE_KEY");
        }
    }

    @Override
    public String keyId() {
        return keyId;
    }

    @Override
    public ECPublicKey publicKey() {
        return publicKey;
    }

    @Override
    public byte[] sign(byte[] signingInput) {
        try {
            var signature = Signature.getInstance("SHA256withECDSA");
            signature.initSign(privateKey);
            signature.update(signingInput);
            return ECDSA.transcodeSignatureToConcat(signature.sign(), Es256.SIGNATURE_LENGTH);
        } catch (GeneralSecurityException | JOSEException e) {
            throw new IllegalStateException("Could not sign a run token (" + e.getClass().getSimpleName() + ")");
        }
    }

    /** RFC 7638 thumbprint of a P-256 public key; refuses other curves. */
    static String thumbprint(ECPublicKey key) {
        if (!Curve.P_256.equals(Curve.forECParameterSpec(key.getParams()))) {
            throw new IllegalStateException("Run token keys must be on curve P-256 (ES256)");
        }
        try {
            return new ECKey.Builder(Curve.P_256, key).build().computeThumbprint().toString();
        } catch (JOSEException e) {
            throw new IllegalStateException("Could not compute the run token key id", e);
        }
    }

    /** PEM (any header) or a bare base64 body; literal "\n" (from .env files) counts as a line break. */
    static byte[] der(String pem) {
        var body = pem.replace("\\n", "\n").replaceAll("-----(BEGIN|END) [A-Z ]+-----", "").replaceAll("\\s", "");
        return Base64.getDecoder().decode(body);
    }
}
