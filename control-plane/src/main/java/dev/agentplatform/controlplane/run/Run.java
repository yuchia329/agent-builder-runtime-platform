package dev.agentplatform.controlplane.run;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * One execution of an agent version. Free-form parts ({@code inputJson},
 * {@code pendingApprovalJson}) are kept as JSON text; nullable fields are absent until reported.
 */
public record Run(
        String runId,
        String tenantId,
        String agentName,
        int agentVersion,
        RunStatus status,
        int steps,
        BigDecimal costUsd,
        String traceId,
        String pendingApprovalJson,
        Failure failure,
        String output,
        String inputJson,
        Instant createdAt,
        Instant updatedAt) {

    public record Failure(String reason, String message) {}

    public static Run queued(String runId, String tenantId, String agentName, int agentVersion,
                             String inputJson, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, RunStatus.QUEUED, 0, BigDecimal.ZERO,
                null, null, null, null, inputJson, now, now);
    }

    /** Applies an update; null fields in the update leave the current value. */
    public Run with(RunUpdate update, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, update.status(),
                update.steps() != null ? update.steps() : steps,
                update.costUsd() != null ? update.costUsd() : costUsd,
                update.traceId() != null ? update.traceId() : traceId,
                update.pendingApprovalJson() != null ? update.pendingApprovalJson() : pendingApprovalJson,
                update.failure() != null ? update.failure() : failure,
                update.output() != null ? update.output() : output,
                inputJson, createdAt, now);
    }
}
