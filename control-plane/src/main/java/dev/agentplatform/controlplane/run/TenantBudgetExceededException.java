package dev.agentplatform.controlplane.run;

import java.math.BigDecimal;

/** {@code POST /runs} refused: the tenant has reached its daily spend cap (ADR 0026). */
public class TenantBudgetExceededException extends RuntimeException {

    public static final String REASON = "tenant_budget";

    private final int retryAfterSeconds;

    public TenantBudgetExceededException(String tenantId, BigDecimal dailyUsd, int retryAfterSeconds) {
        super("Tenant '%s' has reached its daily spend cap of $%s; new runs start again after 00:00 UTC"
                .formatted(tenantId, dailyUsd.toPlainString()));
        this.retryAfterSeconds = retryAfterSeconds;
    }

    /** Seconds until the next UTC day, when the cap resets. */
    public int retryAfterSeconds() {
        return retryAfterSeconds;
    }
}
