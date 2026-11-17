package dev.agentplatform.controlplane.agent;

import java.time.Instant;

/** One immutable published spec. {@code specJson} is the validated spec as JSON. */
public record AgentVersion(
        String tenantId,
        String agentName,
        int version,
        String trigger,
        String specJson,
        Instant createdAt) {}
