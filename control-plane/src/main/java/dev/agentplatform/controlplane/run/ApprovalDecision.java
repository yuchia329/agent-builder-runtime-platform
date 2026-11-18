package dev.agentplatform.controlplane.run;

import java.time.Duration;
import java.time.Instant;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * A person's decision on a run's pending call (ApprovalDecision in
 * contracts/internal-api.openapi.yaml). Step, tool and arguments are copied from the stored
 * pending call, never taken from the request, so the worker's match check compares against
 * exactly what it paused on.
 */
public record ApprovalDecision(int step, String tool, JsonNode arguments, Decision decision, String decidedBy,
                               Instant decidedAt) {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    public static final String REJECTED_REASON = "approval_rejected";
    /** Set by the approval sweep when nobody decided in time; not a decision, so no {@code approval}. */
    public static final String EXPIRED_REASON = "approval_expired";

    /** Lower case, as on the wire. */
    public enum Decision { approved, rejected }

    /** The decision on {@code pendingApprovalJson} (a PendingApproval). */
    public static ApprovalDecision of(String pendingApprovalJson, Decision decision, String decidedBy, Instant at) {
        var pending = JSON.readTree(pendingApprovalJson);
        return new ApprovalDecision(pending.get("step").asInt(), pending.get("tool").asString(),
                pending.get("arguments"), decision, decidedBy, at);
    }

    /** The failure a run ends with when its pending call waited longer than {@code timeout}. */
    public static Run.Failure expiredFailure(String pendingApprovalJson, Duration timeout) {
        var pending = JSON.readTree(pendingApprovalJson);
        return new Run.Failure(EXPIRED_REASON, "Call to %s at step %d got no decision within %d h".formatted(
                pending.get("tool").asString(), pending.get("step").asInt(), timeout.toHours()));
    }

    public boolean approved() {
        return decision == Decision.approved;
    }

    /** QUEUED to resume and make the call, or FAILED. */
    public RunStatus nextStatus() {
        return approved() ? RunStatus.QUEUED : RunStatus.FAILED;
    }

    /** The failure a rejected run ends with. */
    public Run.Failure failure() {
        return new Run.Failure(REJECTED_REASON,
                "Call to %s at step %d was rejected by %s".formatted(tool, step, decidedBy));
    }

    public String toJson() {
        var node = JSON.createObjectNode();
        node.put("step", step);
        node.put("tool", tool);
        node.set("arguments", arguments);
        node.put("decision", decision.name());
        node.put("decided_by", decidedBy);
        node.put("decided_at", decidedAt.toString());
        return JSON.writeValueAsString(node);
    }
}
