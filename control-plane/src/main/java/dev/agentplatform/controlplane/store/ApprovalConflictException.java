package dev.agentplatform.controlplane.store;

/** A decision names a pending call the run is no longer waiting on (a later pause replaced it). */
public class ApprovalConflictException extends RuntimeException {

    public ApprovalConflictException(String message) {
        super(message);
    }
}
