package dev.agentplatform.controlplane.store;

public class RunNotFoundException extends RuntimeException {

    public RunNotFoundException(String runId) {
        super("Run '%s' not found".formatted(runId));
    }
}
