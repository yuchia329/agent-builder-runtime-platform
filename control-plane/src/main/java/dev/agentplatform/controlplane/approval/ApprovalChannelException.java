package dev.agentplatform.controlplane.approval;

/** An approval request was not posted. The message is safe to log (no credentials). */
public class ApprovalChannelException extends RuntimeException {

    public ApprovalChannelException(String message) {
        super(message);
    }
}
