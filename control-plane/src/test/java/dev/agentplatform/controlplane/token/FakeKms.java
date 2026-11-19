package dev.agentplatform.controlplane.token;

import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.security.Signature;
import java.util.concurrent.atomic.AtomicInteger;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.GetPublicKeyRequest;
import software.amazon.awssdk.services.kms.model.GetPublicKeyResponse;
import software.amazon.awssdk.services.kms.model.KeySpec;
import software.amazon.awssdk.services.kms.model.KeyUsageType;
import software.amazon.awssdk.services.kms.model.KmsException;
import software.amazon.awssdk.services.kms.model.MessageType;
import software.amazon.awssdk.services.kms.model.NotFoundException;
import software.amazon.awssdk.services.kms.model.SignRequest;
import software.amazon.awssdk.services.kms.model.SignResponse;
import software.amazon.awssdk.services.kms.model.SigningAlgorithmSpec;

/**
 * One asymmetric KMS key in memory: {@code GetPublicKey} answers the public key as X.509 DER, and
 * {@code Sign} signs a SHA-256 digest (message type DIGEST, ECDSA_SHA_256) and answers DER, as KMS
 * does. Any other key reference is not found. Counts calls.
 */
public class FakeKms implements KmsClient {

    public static final String ALIAS = "alias/test-run-token";

    public final TestKeys keys;
    public final AtomicInteger publicKeyCalls = new AtomicInteger();
    public final AtomicInteger signCalls = new AtomicInteger();
    private final KeySpec keySpec;
    private final KeyUsageType keyUsage;

    public FakeKms() {
        this(TestKeys.p256(), KeySpec.ECC_NIST_P256, KeyUsageType.SIGN_VERIFY);
    }

    public FakeKms(TestKeys keys, KeySpec keySpec, KeyUsageType keyUsage) {
        this.keys = keys;
        this.keySpec = keySpec;
        this.keyUsage = keyUsage;
    }

    public KeyPair pair() {
        return keys.pair;
    }

    @Override
    public GetPublicKeyResponse getPublicKey(GetPublicKeyRequest request) {
        publicKeyCalls.incrementAndGet();
        known(request.keyId());
        return GetPublicKeyResponse.builder()
                .keyId("arn:aws:kms:us-east-1:000000000000:key/test")
                .keySpec(keySpec)
                .keyUsage(keyUsage)
                .signingAlgorithmsWithStrings("ECDSA_SHA_256")
                .publicKey(SdkBytes.fromByteArray(keys.pair.getPublic().getEncoded()))
                .build();
    }

    @Override
    public SignResponse sign(SignRequest request) {
        signCalls.incrementAndGet();
        known(request.keyId());
        if (request.signingAlgorithm() != SigningAlgorithmSpec.ECDSA_SHA_256
                || request.messageType() != MessageType.DIGEST || request.message().asByteArray().length != 32) {
            throw KmsException.builder().message("InvalidSigningAlgorithm or digest length").build();
        }
        try {
            var signature = Signature.getInstance("NONEwithECDSA");
            signature.initSign(keys.pair.getPrivate());
            signature.update(request.message().asByteArray());
            return SignResponse.builder()
                    .keyId(request.keyId())
                    .signingAlgorithm(SigningAlgorithmSpec.ECDSA_SHA_256)
                    .signature(SdkBytes.fromByteArray(signature.sign()))
                    .build();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void known(String keyId) {
        if (!ALIAS.equals(keyId)) {
            throw NotFoundException.builder().message("Alias " + keyId + " is not found.").build();
        }
    }

    @Override
    public String serviceName() {
        return "kms";
    }

    @Override
    public void close() {}
}
