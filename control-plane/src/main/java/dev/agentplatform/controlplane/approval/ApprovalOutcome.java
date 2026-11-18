package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import java.time.Instant;

/**
 * What an approval request ended as, shown in place of its buttons: a decision (who, when), or a
 * note when the request no longer applies (the run moved on before this click).
 */
public record ApprovalOutcome(Decision decision, String decidedBy, Instant decidedAt, String note) {

    public static ApprovalOutcome decided(Decision decision, String decidedBy, Instant decidedAt) {
        return new ApprovalOutcome(decision, decidedBy, decidedAt, null);
    }

    public static ApprovalOutcome noLongerApplies(String note) {
        return new ApprovalOutcome(null, null, null, note);
    }

    public boolean isDecision() {
        return decision != null;
    }
}
