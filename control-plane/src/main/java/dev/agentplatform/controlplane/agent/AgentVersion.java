package dev.agentplatform.controlplane.agent;

import java.time.Instant;

/**
 * One immutable published spec. {@code specJson} is the validated spec as JSON; {@code owner} is
 * who published it ({@code <kind>:<id>}, see {@link dev.agentplatform.controlplane.tenant.Principals}),
 * whose credentials a run with {@code acts_as: owner} uses.
 */
public record AgentVersion(
        String tenantId,
        String agentName,
        int version,
        String trigger,
        String specJson,
        String owner,
        Instant createdAt) {}
