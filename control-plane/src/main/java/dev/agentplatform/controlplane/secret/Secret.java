package dev.agentplatform.controlplane.secret;

/**
 * The control plane's secrets. Locally each is the environment variable of the same name; on AWS
 * the SSM parameter {@code <SECRETS_SSM_PATH>/<parameter>} (ADR 0018; created by
 * infra/envs/dev/ssm.tf).
 */
public enum Secret {
    TENANT_API_KEY("tenant-api-key"),
    GITHUB_WEBHOOK_SECRET("github-webhook-secret"),
    SLACK_BOT_TOKEN("slack-bot-token"),
    SLACK_SIGNING_SECRET("slack-signing-secret");

    private final String parameter;

    Secret(String parameter) {
        this.parameter = parameter;
    }

    /** The parameter's name under the SSM path. */
    public String parameter() {
        return parameter;
    }
}
