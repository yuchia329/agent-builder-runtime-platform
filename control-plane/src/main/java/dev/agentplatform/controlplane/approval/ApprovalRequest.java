package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.Run;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** What a person is asked to decide: a run's pending call (PendingApproval in the internal API). */
public record ApprovalRequest(String runId, String tenantId, String agentName, int step, String tool,
                              JsonNode arguments) {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    /** The request for {@code run}'s pending call. */
    public static ApprovalRequest of(Run run) {
        if (run.pendingApprovalJson() == null) {
            throw new IllegalArgumentException("Run '%s' has no pending call".formatted(run.runId()));
        }
        var pending = JSON.readTree(run.pendingApprovalJson());
        return new ApprovalRequest(run.runId(), run.tenantId(), run.agentName(), pending.get("step").asInt(),
                pending.get("tool").asString(), pending.get("arguments"));
    }
}
