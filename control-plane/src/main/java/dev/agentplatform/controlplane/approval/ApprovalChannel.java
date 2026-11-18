package dev.agentplatform.controlplane.approval;

/**
 * Port: asks a person to approve or reject a pending call (ADR 0002). Every adapter must pass
 * {@code ApprovalChannelContractTest}. Decisions come back through the control plane's own routes
 * ({@code POST /runs/{id}/approval}, and from Slack {@code POST /approvals/slack}).
 */
public interface ApprovalChannel {

    /**
     * Posts the request with Approve and Reject choices.
     *
     * @return where the request was posted
     * @throws ApprovalChannelException if it was not posted; the message never contains credentials
     */
    ApprovalMessage post(ApprovalRequest request);
}
