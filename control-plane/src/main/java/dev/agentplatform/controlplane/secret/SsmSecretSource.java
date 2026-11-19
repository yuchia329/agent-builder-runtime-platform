package dev.agentplatform.controlplane.secret;

import java.util.Arrays;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.ssm.SsmClient;

/**
 * Secrets from SSM Parameter Store SecureString parameters under one path (ADR 0018), for example
 * {@code /agent-platform/dev/control-plane}. Nothing is read at startup: the first {@link #get}
 * (or {@link #prefetch} after a SnapStart restore) loads every secret in one {@code GetParameters}
 * call with decryption, and the values stay in memory for the life of the environment. Rotating a
 * secret means putting the new value and starting new environments (publish a new version). A
 * failed load is not kept, so the next request tries again.
 *
 * <p>Every secret counts as configured: Terraform creates all of them. A parameter that is missing
 * reads as not set (logged by name, never by value).
 */
public class SsmSecretSource implements SecretSource {

    private static final Logger log = LoggerFactory.getLogger(SsmSecretSource.class);

    private final SsmClient ssm;
    private final String path;
    private final Object lock = new Object();
    private volatile Map<Secret, String> values; // null until loaded

    public SsmSecretSource(SsmClient ssm, String path) {
        if (path == null || !path.startsWith("/")) {
            throw new IllegalStateException("SECRETS_SSM_PATH must be an SSM path like /agent-platform/dev/control-plane");
        }
        this.ssm = ssm;
        this.path = path.replaceAll("/+$", "");
    }

    @Override
    public String get(Secret secret) {
        return load().get(secret);
    }

    @Override
    public String peek(Secret secret) {
        var loaded = values;
        return loaded == null ? null : loaded.get(secret);
    }

    @Override
    public boolean configured(Secret secret) {
        return true;
    }

    @Override
    public void prefetch() {
        Thread.ofVirtual().name("secrets-prefetch").start(() -> {
            try {
                load();
            } catch (SecretUnavailableException e) {
                log.warn("Prefetching secrets failed; the first request tries again: {}", e.getMessage());
            }
        });
    }

    private Map<Secret, String> load() {
        var loaded = values;
        if (loaded != null) {
            return loaded;
        }
        synchronized (lock) {
            if (values == null) {
                values = fetch();
            }
            return values;
        }
    }

    private Map<Secret, String> fetch() {
        var byName = new HashMap<String, Secret>();
        Arrays.stream(Secret.values()).forEach(secret -> byName.put(path + "/" + secret.parameter(), secret));
        var started = System.nanoTime();
        try {
            var response = ssm.getParameters(r -> r.names(byName.keySet()).withDecryption(true));
            var result = new EnumMap<Secret, String>(Secret.class);
            response.parameters().forEach(p -> result.put(byName.get(p.name()), p.value()));
            if (response.hasInvalidParameters() && !response.invalidParameters().isEmpty()) {
                log.warn("SSM parameters missing (their features stay off): {}", response.invalidParameters());
            }
            log.info("Loaded {} secrets from SSM in {} ms", result.size(), (System.nanoTime() - started) / 1_000_000);
            return result;
        } catch (SdkException e) {
            // The SDK's message names the operation and error code, never a parameter value.
            throw new SecretUnavailableException("Could not read secrets from SSM under " + path + ": "
                    + e.getClass().getSimpleName(), e);
        }
    }
}
