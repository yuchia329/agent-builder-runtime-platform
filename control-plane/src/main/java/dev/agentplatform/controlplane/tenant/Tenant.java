package dev.agentplatform.controlplane.tenant;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.regex.Pattern;

/**
 * A tenant and its limits (ADR 0026): {@code maxRunning} runs executing at once and
 * {@code dailyUsd} model spend per UTC day. Limits are enforced when a run starts.
 */
public record Tenant(String id, TenantLimits limits, Instant createdAt) {

    /** Lowercase letters, digits and dashes, starting with a letter or digit, at most 64. */
    public static final Pattern ID = Pattern.compile("^[a-z0-9][a-z0-9-]{0,63}$");

    public Tenant {
        requireValidId(id);
        if (limits == null || createdAt == null) {
            throw new IllegalArgumentException("limits and createdAt are required");
        }
    }

    public static void requireValidId(String id) {
        if (id == null || !ID.matcher(id).matches()) {
            throw new IllegalArgumentException(
                    "tenant id must be lowercase letters, digits and dashes (1-64), got: " + id);
        }
    }

    /** Limits of one tenant; both must be positive. */
    public record TenantLimits(int maxRunning, BigDecimal dailyUsd) {

        public TenantLimits {
            if (maxRunning < 1) {
                throw new IllegalArgumentException("maxRunning must be 1 or more");
            }
            if (dailyUsd == null || dailyUsd.signum() <= 0) {
                throw new IllegalArgumentException("dailyUsd must be above 0");
            }
        }
    }
}
