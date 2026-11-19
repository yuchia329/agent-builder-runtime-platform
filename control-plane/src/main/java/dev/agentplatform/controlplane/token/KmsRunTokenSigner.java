package dev.agentplatform.controlplane.token;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.crypto.impl.ECDSA;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.interfaces.ECPublicKey;
import java.security.spec.X509EncodedKeySpec;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.KeySpec;
import software.amazon.awssdk.services.kms.model.KeyUsageType;
import software.amazon.awssdk.services.kms.model.MessageType;
import software.amazon.awssdk.services.kms.model.SigningAlgorithmSpec;

/**
 * Signs with a KMS key ({@code RUN_TOKEN_KMS_KEY_ID}: key id, ARN or alias; ADR 0018): an
 * {@code ECC_NIST_P256} key for {@code SIGN_VERIFY}. The private key never leaves KMS. The input is
 * hashed here (SHA-256) and KMS signs the digest ({@code ECDSA_SHA_256}, message type DIGEST), so a
 * token of any size is one small request. KMS answers DER; JOSE wants R and S side by side, so the
 * signature is transcoded.
 *
 * <p>Nothing is called at construction: the public key (and from it {@link #keyId()}) is fetched
 * on first use with {@code kms:GetPublicKey} and kept. A key of the wrong kind fails then. Needs
 * {@code kms:Sign} and {@code kms:GetPublicKey} on the key.
 */
public class KmsRunTokenSigner implements RunTokenSigner {

    private static final Logger log = LoggerFactory.getLogger(KmsRunTokenSigner.class);

    private record PublicKey(ECPublicKey key, String keyId) {}

    private final KmsClient kms;
    private final String keyRef;
    private final Object lock = new Object();
    private volatile PublicKey publicKey; // null until fetched

    public KmsRunTokenSigner(KmsClient kms, String keyRef) {
        this.kms = kms;
        this.keyRef = keyRef;
    }

    @Override
    public String keyId() {
        return loadPublicKey().keyId();
    }

    @Override
    public ECPublicKey publicKey() {
        return loadPublicKey().key();
    }

    @Override
    public byte[] sign(byte[] signingInput) {
        byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256").digest(signingInput);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("SHA-256 is not available", e);
        }
        try {
            var der = kms.sign(r -> r.keyId(keyRef)
                    .message(SdkBytes.fromByteArray(digest))
                    .messageType(MessageType.DIGEST)
                    .signingAlgorithm(SigningAlgorithmSpec.ECDSA_SHA_256)).signature().asByteArray();
            return ECDSA.transcodeSignatureToConcat(der, Es256.SIGNATURE_LENGTH);
        } catch (SdkException e) {
            throw new IllegalStateException("KMS could not sign a run token with " + keyRef + " ("
                    + e.getClass().getSimpleName() + ")", e);
        } catch (JOSEException e) {
            throw new IllegalStateException("KMS returned a signature that is not ECDSA P-256", e);
        }
    }

    private PublicKey loadPublicKey() {
        var loaded = publicKey;
        if (loaded != null) {
            return loaded;
        }
        synchronized (lock) {
            if (publicKey == null) {
                publicKey = fetchPublicKey();
            }
            return publicKey;
        }
    }

    private PublicKey fetchPublicKey() {
        try {
            var response = kms.getPublicKey(r -> r.keyId(keyRef));
            if (response.keySpec() != KeySpec.ECC_NIST_P256 || response.keyUsage() != KeyUsageType.SIGN_VERIFY) {
                throw new IllegalStateException("KMS key " + keyRef + " must be ECC_NIST_P256 for SIGN_VERIFY, is "
                        + response.keySpecAsString() + " for " + response.keyUsageAsString());
            }
            var key = (ECPublicKey) KeyFactory.getInstance("EC")
                    .generatePublic(new X509EncodedKeySpec(response.publicKey().asByteArray()));
            var keyId = LocalKeySigner.thumbprint(key);
            log.info("Run tokens signed by KMS key {}, key id {}", keyRef, keyId);
            return new PublicKey(key, keyId);
        } catch (SdkException e) {
            throw new IllegalStateException("Could not read the public key of KMS key " + keyRef + " ("
                    + e.getClass().getSimpleName() + ")", e);
        } catch (GeneralSecurityException | ClassCastException e) {
            throw new IllegalStateException("KMS key " + keyRef + " has no usable P-256 public key", e);
        }
    }
}
