package dev.agentplatform.controlplane.token;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.security.interfaces.ECPublicKey;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.kms.model.KeySpec;
import software.amazon.awssdk.services.kms.model.KeyUsageType;

class KmsRunTokenSignerContractTest extends RunTokenSignerContractTest {

    private final FakeKms kms = new FakeKms();

    @Override
    RunTokenSigner signer() {
        return new KmsRunTokenSigner(kms, FakeKms.ALIAS);
    }

    @Override
    ECPublicKey expectedPublicKey() {
        return (ECPublicKey) kms.pair().getPublic();
    }

    @Test
    void nothingIsCalledAtConstruction() {
        signer();

        assertThat(kms.publicKeyCalls).hasValue(0);
        assertThat(kms.signCalls).hasValue(0);
    }

    @Test
    void thePublicKeyIsFetchedOnce() {
        var signer = signer();

        signer.keyId();
        signer.keyId();
        signer.publicKey();

        assertThat(kms.publicKeyCalls).hasValue(1);
    }

    @Test
    void largeTokensAreSignedAsADigest() {
        var input = "x".repeat(10_000).getBytes(StandardCharsets.US_ASCII);

        var signature = signer().sign(input);

        assertThat(Es256.verify(expectedPublicKey(), input, signature)).isTrue();
    }

    @Test
    void aKeyOfTheWrongKindIsRefused() {
        var rsa = new FakeKms(TestKeys.p256(), KeySpec.RSA_2048, KeyUsageType.SIGN_VERIFY);
        var encrypt = new FakeKms(TestKeys.p256(), KeySpec.ECC_NIST_P256, KeyUsageType.ENCRYPT_DECRYPT);

        assertThatThrownBy(() -> new KmsRunTokenSigner(rsa, FakeKms.ALIAS).keyId())
                .hasMessageContaining("must be ECC_NIST_P256 for SIGN_VERIFY");
        assertThatThrownBy(() -> new KmsRunTokenSigner(encrypt, FakeKms.ALIAS).keyId())
                .hasMessageContaining("must be ECC_NIST_P256 for SIGN_VERIFY");
    }

    @Test
    void aP384KeyIsRefused() {
        var p384 = new FakeKms(TestKeys.p384(), KeySpec.ECC_NIST_P256, KeyUsageType.SIGN_VERIFY);

        assertThatThrownBy(() -> new KmsRunTokenSigner(p384, FakeKms.ALIAS).keyId()).hasMessageContaining("P-256");
    }

    @Test
    void kmsErrorsNameTheKey() {
        var missing = new KmsRunTokenSigner(kms, "alias/missing");

        assertThatThrownBy(missing::keyId).hasMessageContaining("alias/missing");
        assertThatThrownBy(() -> missing.sign(new byte[] {1})).hasMessageContaining("alias/missing");
    }
}
