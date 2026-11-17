package dev.agentplatform.controlplane.run;

import java.math.BigDecimal;
import java.time.Instant;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * JSON shapes of a run. Field names become snake_case via the global Jackson setting; null fields
 * are left out. {@link Summary} matches RunSummary and {@link Internal} matches InternalRun in
 * contracts/internal-api.openapi.yaml (checked by tests).
 */
public final class RunViews {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private RunViews() {}

    public record FailureView(String reason, String message) {}

    public record Summary(String runId, String tenantId, String agentName, int agentVersion, RunStatus status,
                          int steps, BigDecimal costUsd, String traceId, JsonNode pendingApproval,
                          FailureView failure, String output, Instant createdAt, Instant updatedAt) {}

    public record Internal(String runId, String tenantId, String agentName, int agentVersion, RunStatus status,
                           int steps, BigDecimal costUsd, String traceId, JsonNode pendingApproval,
                           FailureView failure, String output, Instant createdAt, Instant updatedAt,
                           JsonNode spec, JsonNode input) {}

    public static Summary summary(Run run) {
        return new Summary(run.runId(), run.tenantId(), run.agentName(), run.agentVersion(), run.status(),
                run.steps(), run.costUsd(), run.traceId(), parse(run.pendingApprovalJson()), failure(run),
                run.output(), run.createdAt(), run.updatedAt());
    }

    public static Internal internal(RunService.RunWithSpec loaded) {
        var run = loaded.run();
        return new Internal(run.runId(), run.tenantId(), run.agentName(), run.agentVersion(), run.status(),
                run.steps(), run.costUsd(), run.traceId(), parse(run.pendingApprovalJson()), failure(run),
                run.output(), run.createdAt(), run.updatedAt(), parse(loaded.specJson()), parse(run.inputJson()));
    }

    private static FailureView failure(Run run) {
        return run.failure() == null ? null : new FailureView(run.failure().reason(), run.failure().message());
    }

    private static JsonNode parse(String json) {
        return json == null ? null : JSON.readTree(json);
    }
}
