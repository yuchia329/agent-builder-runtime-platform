package dev.agentplatform.controlplane.secret;

import java.util.EnumMap;
import java.util.Map;

/**
 * Secrets from configuration (environment variables through application.yml): local runs, tests
 * and the local stack. A blank value counts as not set. The tenant API key is required: without it
 * startup fails, as it always has.
 */
public class EnvSecretSource implements SecretSource {

    private final Map<Secret, String> values = new EnumMap<>(Secret.class);

    public EnvSecretSource(Map<Secret, String> values) {
        values.forEach((secret, value) -> {
            if (value != null && !value.isBlank()) {
                this.values.put(secret, value);
            }
        });
        if (!this.values.containsKey(Secret.TENANT_API_KEY)) {
            throw new IllegalStateException("TENANT_API_KEY must be set (or SECRETS_SOURCE=ssm)");
        }
    }

    @Override
    public String get(Secret secret) {
        return values.get(secret);
    }

    @Override
    public String peek(Secret secret) {
        return values.get(secret);
    }

    @Override
    public boolean configured(Secret secret) {
        return values.containsKey(secret);
    }
}
