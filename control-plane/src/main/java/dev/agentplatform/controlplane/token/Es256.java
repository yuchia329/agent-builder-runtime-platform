package dev.agentplatform.controlplane.token;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.crypto.impl.ECDSA;
import java.security.GeneralSecurityException;
import java.security.Signature;
import java.security.interfaces.ECPublicKey;

/** ES256 helpers shared by the signers. */
final class Es256 {

    /** R and S, 32 bytes each (JOSE form, RFC 7518 section 3.4). */
    static final int SIGNATURE_LENGTH = 64;

    private Es256() {}

    /** Verifies a JOSE-form signature. */
    static boolean verify(ECPublicKey key, byte[] signingInput, byte[] joseSignature) {
        try {
            var signature = Signature.getInstance("SHA256withECDSA");
            signature.initVerify(key);
            signature.update(signingInput);
            return signature.verify(ECDSA.transcodeSignatureToDER(joseSignature));
        } catch (GeneralSecurityException | JOSEException e) {
            return false;
        }
    }
}
