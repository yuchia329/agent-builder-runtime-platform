package dev.agentplatform.controlplane.secret;

/**
 * Where the control plane's secrets come from (ADR 0018): environment variables locally, SSM
 * Parameter Store on AWS. Callers ask on every use instead of keeping a copy, so a source may load
 * lazily: never at startup (Lambda SnapStart, ADR 0017), on first use, then from memory. Values are
 * never logged.
 *
 * <p>Contract suite: {@code SecretSourceContractTest}.
 */
public interface SecretSource {

    /**
     * The value, or null when the secret is not set (the feature that needs it is then off: its
     * route answers 503, or every API key is refused). May load on first use; throws
     * {@link SecretUnavailableException} if the store cannot be reached (the next call tries again).
     */
    String get(Secret secret);

    /** The value if this source already holds it without loading anything, else null. */
    String peek(Secret secret);

    /**
     * Whether the secret is set up at all, decided at startup without loading: picks the Slack
     * approval channel over the log-only one.
     */
    boolean configured(Secret secret);

    /** Starts loading in the background, so a first request finds the values (after a restore). */
    default void prefetch() {}
}
