package dev.agentplatform.controlplane.run;

/**
 * A start refused for now (ADR 0026): the tenant is at its cap on runs at once. The run stays
 * QUEUED and has been re-queued to arrive after {@code retryAfterSeconds}.
 */
public class RunDeferredException extends RuntimeException {

    public static final String REASON = "tenant_running_limit";

    private final int retryAfterSeconds;

    public RunDeferredException(String runId, int retryAfterSeconds) {
        super("Tenant at its cap on runs at once; run '%s' re-queued to arrive in %d s"
                .formatted(runId, retryAfterSeconds));
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public int retryAfterSeconds() {
        return retryAfterSeconds;
    }
}
