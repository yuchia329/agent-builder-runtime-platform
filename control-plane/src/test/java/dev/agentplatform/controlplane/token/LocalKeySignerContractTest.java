package dev.agentplatform.controlplane.token;

import java.security.interfaces.ECPublicKey;

class LocalKeySignerContractTest extends RunTokenSignerContractTest {

    private final TestKeys keys = TestKeys.p256();

    @Override
    RunTokenSigner signer() {
        return new LocalKeySigner(keys.privatePem, keys.publicPem);
    }

    @Override
    ECPublicKey expectedPublicKey() {
        return (ECPublicKey) keys.pair.getPublic();
    }
}
