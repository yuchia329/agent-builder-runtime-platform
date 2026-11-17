package dev.agentplatform.controlplane.agent;

import dev.agentplatform.controlplane.store.AgentStore;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;

@Service
public class AgentService {

    private final AgentStore store;
    private final SpecValidator validator;

    public AgentService(AgentStore store, SpecValidator validator) {
        this.store = store;
        this.validator = validator;
    }

    public Agent createAgent(String tenantId, String name) {
        return store.createAgent(tenantId, name);
    }

    /** Validates the spec and stores it as the agent's next version, which becomes active. */
    public AgentVersion publishVersion(String tenantId, String agentName, String body, MediaType contentType) {
        var spec = validator.parseAndValidate(body, contentType);
        var specName = spec.path("name").asString();
        if (!agentName.equals(specName)) {
            throw new InvalidSpecException(List.of(
                    "$.name: '%s' does not match agent '%s'".formatted(specName, agentName)));
        }
        var trigger = spec.path("trigger").asString();
        return store.publishVersion(tenantId, agentName, trigger, validator.toJson(spec));
    }
}
