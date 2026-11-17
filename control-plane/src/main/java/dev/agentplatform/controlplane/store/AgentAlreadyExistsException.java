package dev.agentplatform.controlplane.store;

public class AgentAlreadyExistsException extends RuntimeException {

    public AgentAlreadyExistsException(String tenantId, String name) {
        super("Agent '%s' already exists".formatted(name));
    }
}
