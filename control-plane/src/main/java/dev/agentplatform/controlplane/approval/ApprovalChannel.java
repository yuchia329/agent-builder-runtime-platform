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

    /**
     * Replaces the choices of the request posted at {@code message} with its outcome, so nobody
     * clicks a decided call again.
     *
     * @throws ApprovalChannelException if the message was not updated
     */
    void showOutcome(ApprovalMessage message, ApprovalRequest request, ApprovalOutcome outcome);
}
