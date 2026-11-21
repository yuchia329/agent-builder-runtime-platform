package dev.agentplatform.controlplane.store;

/** A run could not start: its tenant already has its cap of runs executing at once (ADR 0026). */
public class TenantAtCapException extends RuntimeException {

    public TenantAtCapException(String tenantId, int maxRunning) {
        super("Tenant '%s' is at its cap of %d runs at once".formatted(tenantId, maxRunning));
    }
}
