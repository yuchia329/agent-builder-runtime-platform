package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.tenant.Principals;
import java.math.BigDecimal;
import java.time.Instant;

/**
 * One execution of an agent version. Free-form parts ({@code inputJson},
 * {@code pendingApprovalJson}, {@code approvalJson}) are kept as JSON text; nullable fields are
 * absent until reported. {@code approvalJson} is the latest decision on a pending call
 * (ApprovalDecision in contracts/internal-api.openapi.yaml). {@code approvalMessage} is where the
 * pending call was posted for a person (absent until posted; each new pause starts without one).
 * {@code startedBy} is what started the run ({@code <kind>:<id>}, see {@link Principals}).
 * {@code setAsideSeconds} is the total delay of its set-asides so far (ADR 0027).
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
        String traceParent,
        String pendingApprovalJson,
        String approvalJson,
        ApprovalMessage approvalMessage,
        Failure failure,
        String output,
        String inputJson,
        String startedBy,
        int setAsideSeconds,
        Instant createdAt,
        Instant updatedAt) {

    public record Failure(String reason, String message) {}

    public static Run queued(String runId, String tenantId, String agentName, int agentVersion,
                             String inputJson, String startedBy, Instant now) {
        return queued(runId, tenantId, agentName, agentVersion, inputJson, startedBy, null, now);
    }

    /**
     * A new run in the trace {@code traceParent} (W3C {@code traceparent} of its "run" span, ADR
     * 0022); its {@code traceId} is that trace's id. Null leaves both unset.
     */
    public static Run queued(String runId, String tenantId, String agentName, int agentVersion,
                             String inputJson, String startedBy, String traceParent, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, RunStatus.QUEUED, 0, BigDecimal.ZERO,
                traceIdOf(traceParent), traceParent, null, null, null, null, null, inputJson, startedBy, 0, now, now);
    }

    /** The trace id (32 hex) inside a W3C {@code traceparent} ({@code 00-<trace>-<span>-<flags>}). */
    public static String traceIdOf(String traceParent) {
        return traceParent == null ? null : traceParent.substring(3, 35);
    }

    /** A run started through the API with the tenant's key. */
    public static Run queued(String runId, String tenantId, String agentName, int agentVersion,
                             String inputJson, Instant now) {
        return queued(runId, tenantId, agentName, agentVersion, inputJson, Principals.api(tenantId), now);
    }

    /**
     * Applies an update; null fields in the update leave the current value. A worker's trace id is
     * kept only if the run has none (runs created before the control plane started traces). A new pause
     * (WAITING_APPROVAL) has not been posted yet, so it drops the previous approval message.
     */
    public Run with(RunUpdate update, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, update.status(),
                update.steps() != null ? update.steps() : steps,
                update.costUsd() != null ? update.costUsd() : costUsd,
                traceId != null ? traceId : update.traceId(),
                traceParent,
                update.pendingApprovalJson() != null ? update.pendingApprovalJson() : pendingApprovalJson,
                approvalJson,
                update.status() == RunStatus.WAITING_APPROVAL ? null : approvalMessage,
                update.failure() != null ? update.failure() : failure,
                update.output() != null ? update.output() : output,
                inputJson, startedBy, setAsideSeconds, createdAt, now);
    }

    /**
     * Applies a decision on the pending call: clears it, records the decision, and moves the run
     * to QUEUED (approved) or FAILED {@code approval_rejected} (rejected).
     */
    public Run decided(ApprovalDecision decision, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, decision.nextStatus(), steps, costUsd, traceId,
                traceParent, null, decision.toJson(), approvalMessage, decision.approved() ? failure : decision.failure(), output,
                inputJson, startedBy, setAsideSeconds, createdAt, now);
    }

    /** Fails the run with {@code failure} ({@code approval_expired}) and clears the pending call. */
    public Run expired(Failure failure, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, RunStatus.FAILED, steps, costUsd, traceId, traceParent,
                null, approvalJson, approvalMessage, failure, output, inputJson, startedBy, setAsideSeconds, createdAt, now);
    }

    /** The run with its pending call posted at {@code message}. */
    public Run withApprovalMessage(ApprovalMessage message) {
        return new Run(runId, tenantId, agentName, agentVersion, status, steps, costUsd, traceId, traceParent,
                pendingApprovalJson,
                approvalJson, message, failure, output, inputJson, startedBy, setAsideSeconds, createdAt, updatedAt);
    }

    /** Set aside (ADR 0027): back to QUEUED, {@code delaySeconds} added to the total set aside. */
    public Run setAside(int delaySeconds, Instant now) {
        return new Run(runId, tenantId, agentName, agentVersion, RunStatus.QUEUED, steps, costUsd, traceId, traceParent,
                pendingApprovalJson, approvalJson, approvalMessage, failure, output, inputJson, startedBy,
                setAsideSeconds + delaySeconds, createdAt, now);
    }
}
