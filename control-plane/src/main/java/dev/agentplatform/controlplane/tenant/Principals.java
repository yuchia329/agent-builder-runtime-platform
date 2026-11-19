package dev.agentplatform.controlplane.tenant;

/**
 * Identities as {@code <kind>:<id>} (principal in contracts/run-token.schema.json): who published a
 * version ({@code owner}) and what started a run ({@code started_by}). Until users exist (RBAC,
 * after M4) the only caller identity is the tenant's API key.
 */
public final class Principals {

    /** A run started by a GitHub webhook delivery. */
    public static final String WEBHOOK_GITHUB = "webhook:github";

    private Principals() {}

    /** The tenant's API key, the identity behind every API call today. */
    public static String api(String tenantId) {
        return "api:" + tenantId;
    }
}
