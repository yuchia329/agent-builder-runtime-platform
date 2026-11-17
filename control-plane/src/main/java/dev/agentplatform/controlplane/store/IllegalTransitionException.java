package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.RunStatus;

public class IllegalTransitionException extends RuntimeException {

    private final RunStatus current;
    private final RunStatus requested;

    public IllegalTransitionException(String runId, RunStatus current, RunStatus requested) {
        super("Run '%s' is %s and cannot move to %s".formatted(runId, current, requested));
        this.current = current;
        this.requested = requested;
    }

    public RunStatus current() {
        return current;
    }

    public RunStatus requested() {
        return requested;
    }
}
