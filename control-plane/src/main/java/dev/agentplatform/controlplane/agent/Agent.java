package dev.agentplatform.controlplane.agent;

import java.time.Instant;

/**
 * An agent owned by a tenant. {@code name} is the agent id, unique within the tenant.
 * {@code latestVersion} is 0 before the first publish; {@code activeVersion} and {@code trigger}
 * are null until then.
 */
public record Agent(
        String tenantId,
        String name,
        int latestVersion,
        Integer activeVersion,
        String trigger,
        Instant createdAt) {}
