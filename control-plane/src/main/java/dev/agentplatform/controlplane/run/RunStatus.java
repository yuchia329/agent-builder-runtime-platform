package dev.agentplatform.controlplane.run;

import java.util.EnumSet;
import java.util.Set;

/**
 * Run states (docs/product.md). The changes a worker may request through the internal API are
 * listed in contracts/internal-api.openapi.yaml; {@link #allowedFrom} encodes that table.
 */
public enum RunStatus {
    QUEUED, RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED;

    /** Statuses a run may be in for a worker to move it to {@code this}. Empty: never via the API. */
    public Set<RunStatus> allowedFrom() {
        return switch (this) {
            case RUNNING -> EnumSet.of(QUEUED, RUNNING);
            case WAITING_APPROVAL, SUCCEEDED, FAILED -> EnumSet.of(RUNNING);
            case QUEUED -> EnumSet.noneOf(RunStatus.class);
        };
    }
}
