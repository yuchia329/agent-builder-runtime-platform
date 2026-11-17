package dev.agentplatform.controlplane.queue;

import tools.jackson.databind.json.JsonMapper;

/** Body of a run queue message (contracts/run-message.schema.json). */
public record RunMessage(String runId, String tenantId) {

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
