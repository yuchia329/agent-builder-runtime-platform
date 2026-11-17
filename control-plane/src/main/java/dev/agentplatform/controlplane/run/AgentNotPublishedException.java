package dev.agentplatform.controlplane.run;

public class AgentNotPublishedException extends RuntimeException {

    public AgentNotPublishedException(String agentName) {
        super("Agent '%s' has no published version".formatted(agentName));
    }
}
