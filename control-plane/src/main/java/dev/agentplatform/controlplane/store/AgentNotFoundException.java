package dev.agentplatform.controlplane.store;

public class AgentNotFoundException extends RuntimeException {

    public AgentNotFoundException(String tenantId, String name) {
        super("Agent '%s' not found".formatted(name));
    }
}
