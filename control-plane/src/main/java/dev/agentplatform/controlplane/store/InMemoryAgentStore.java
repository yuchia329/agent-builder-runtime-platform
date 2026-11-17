package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.agent.Agent;
import dev.agentplatform.controlplane.agent.AgentVersion;
import java.time.Clock;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;

/** {@link AgentStore} for tests and local runs without DynamoDB. Thread-safe; data is lost on exit. */
public class InMemoryAgentStore implements AgentStore {

    private record AgentKey(String tenantId, String name) {}

    private record VersionKey(String tenantId, String name, int version) {}

    private final Clock clock;
    private final Map<AgentKey, Agent> agents = new HashMap<>();
    private final Map<VersionKey, AgentVersion> versions = new HashMap<>();

    public InMemoryAgentStore(Clock clock) {
        this.clock = clock;
    }

    @Override
    public synchronized Agent createAgent(String tenantId, String name) {
        var key = new AgentKey(tenantId, name);
        if (agents.containsKey(key)) {
            throw new AgentAlreadyExistsException(tenantId, name);
        }
        var agent = new Agent(tenantId, name, 0, null, null, clock.instant());
        agents.put(key, agent);
        return agent;
    }

    @Override
    public synchronized Optional<Agent> findAgent(String tenantId, String name) {
        return Optional.ofNullable(agents.get(new AgentKey(tenantId, name)));
    }

    @Override
    public synchronized AgentVersion publishVersion(String tenantId, String name, String trigger, String specJson) {
        var key = new AgentKey(tenantId, name);
        var agent = agents.get(key);
        if (agent == null) {
            throw new AgentNotFoundException(tenantId, name);
        }
        int next = agent.latestVersion() + 1;
        var version = new AgentVersion(tenantId, name, next, trigger, specJson, clock.instant());
        versions.put(new VersionKey(tenantId, name, next), version);
        agents.put(key, new Agent(tenantId, name, next, next, trigger, agent.createdAt()));
        return version;
    }

    @Override
    public synchronized Optional<AgentVersion> findVersion(String tenantId, String name, int version) {
        return Optional.ofNullable(versions.get(new VersionKey(tenantId, name, version)));
    }

    @Override
    public synchronized List<Agent> findAgentsByTrigger(String trigger) {
        var result = new ArrayList<Agent>();
        for (var agent : agents.values()) {
            if (Objects.equals(agent.trigger(), trigger)) {
                result.add(agent);
            }
        }
        return result;
    }
}
