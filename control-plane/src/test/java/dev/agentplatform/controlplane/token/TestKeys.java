package dev.agentplatform.controlplane.token;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.spec.ECGenParameterSpec;
import java.util.Base64;

/** Throwaway P-256 key pairs as PEM, like `openssl` would write them. */
public final class TestKeys {

    public final KeyPair pair;
    public final String privatePem;
    public final String publicPem;

    private TestKeys(String curve) {
        try {
            var generator = KeyPairGenerator.getInstance("EC");
            generator.initialize(new ECGenParameterSpec(curve));
            pair = generator.generateKeyPair();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        privatePem = pem("PRIVATE KEY", pair.getPrivate().getEncoded());
        publicPem = pem("PUBLIC KEY", pair.getPublic().getEncoded());
    }

    public static TestKeys p256() {
        return new TestKeys("secp256r1");
    }

    public static TestKeys p384() {
        return new TestKeys("secp384r1");
    }

    private static String pem(String type, byte[] der) {
        var body = Base64.getMimeEncoder(64, "\n".getBytes()).encodeToString(der);
        return "-----BEGIN " + type + "-----\n" + body + "\n-----END " + type + "-----\n";
    }
}
