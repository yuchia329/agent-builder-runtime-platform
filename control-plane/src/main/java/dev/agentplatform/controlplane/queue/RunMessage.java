package dev.agentplatform.controlplane.queue;

import tools.jackson.databind.json.JsonMapper;

/**
 * A run queue message: the body (contracts/run-message.schema.json) plus the run's trace context
 * ({@code traceParent}, W3C {@code traceparent}), sent as the message attribute
 * {@value #TRACE_ATTRIBUTE}, never in the body (ADR 0022). {@code traceParent} may be null.
 */
public record RunMessage(String runId, String tenantId, String traceParent) {

    /** SQS message attribute (String) that carries {@link #traceParent()}. */
    public static final String TRACE_ATTRIBUTE = "traceparent";

    public RunMessage(String runId, String tenantId) {
        this(runId, tenantId, null);
    }

    public static final int SCHEMA_VERSION = 1;
    private static final JsonMapper JSON = JsonMapper.builder().build();

    public String toJson() {
        var node = JSON.createObjectNode();
        node.put("schema_version", SCHEMA_VERSION);
        node.put("run_id", runId);
        node.put("tenant_id", tenantId);
        return JSON.writeValueAsString(node);
    }
}
