package dev.agentplatform.controlplane.token;

import java.security.interfaces.ECPublicKey;

/**
 * Signs run tokens with a P-256 key (ES256; ADR 0003, ADR 0018): a key pair from configuration
 * locally ({@link LocalKeySigner}), a KMS key on AWS ({@link KmsRunTokenSigner}). The gateway
 * verifies with the public key and finds it by {@link #keyId()}, the key's RFC 7638 thumbprint.
 * Implementations make no network call at construction (Lambda SnapStart, ADR 0017).
 *
 * <p>Contract suite: {@code RunTokenSignerContractTest}.
 */
public interface RunTokenSigner {

    /** The {@code kid} tokens carry: the RFC 7638 thumbprint of {@link #publicKey()}. */
    String keyId();

    /** The key tokens verify with. */
    ECPublicKey publicKey();

    /**
     * The ES256 signature of a JWS signing input ({@code header.payload}), in JOSE form: R and S,
     * 32 bytes each.
     */
    byte[] sign(byte[] signingInput);
}
