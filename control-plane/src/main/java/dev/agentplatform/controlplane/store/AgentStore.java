package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.agent.Agent;
import dev.agentplatform.controlplane.agent.AgentVersion;
import java.util.List;
import java.util.Optional;

/**
 * Port for agents and their versions (ADR 0004, ADR 0008). Every adapter must pass
 * {@code AgentStoreContractTest}. Behavior every adapter guarantees:
 *
 * <ul>
 *   <li>Agent names are unique per tenant; tenants never see each other's agents.
 *   <li>Version numbers are 1, 2, 3, ... per agent with no gaps or repeats, even under concurrent
 *       publishes.
 *   <li>A version never changes once written.
 *   <li>Publishing makes the new version active and indexes the agent under that version's trigger.
 * </ul>
 */
public interface AgentStore {

    /** @throws AgentAlreadyExistsException if the tenant already has an agent with this name */
    Agent createAgent(String tenantId, String name);

    Optional<Agent> findAgent(String tenantId, String name);

    /**
     * Stores the spec as the agent's next version and makes it the active version.
     *
     * @throws AgentNotFoundException if the agent does not exist
     */
    AgentVersion publishVersion(String tenantId, String name, String trigger, String specJson);

    Optional<AgentVersion> findVersion(String tenantId, String name, int version);

    /** Agents in any tenant whose active version has this trigger. */
    List<Agent> findAgentsByTrigger(String trigger);
}
