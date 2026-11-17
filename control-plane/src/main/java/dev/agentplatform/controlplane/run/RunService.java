package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.queue.RunMessage;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.AgentNotFoundException;
import dev.agentplatform.controlplane.store.AgentStore;
import dev.agentplatform.controlplane.store.RunNotFoundException;
import dev.agentplatform.controlplane.store.RunStore;
import dev.agentplatform.controlplane.agent.Agent;
import java.time.Clock;
import java.util.Comparator;
import java.util.List;
import java.util.HexFormat;
import java.security.SecureRandom;
import org.springframework.stereotype.Service;

@Service
public class RunService {

    private static final SecureRandom RANDOM = new SecureRandom();

    private final AgentStore agents;
    private final RunStore runs;
    private final RunQueuePublisher queue;
    private final Clock clock;

    public RunService(AgentStore agents, RunStore runs, RunQueuePublisher queue, Clock clock) {
        this.agents = agents;
        this.runs = runs;
        this.queue = queue;
        this.clock = clock;
    }

    /** A run plus the spec of the version it is pinned to, for workers. */
    public record RunWithSpec(Run run, String specJson) {}

    /**
     * Stores a QUEUED run pinned to the agent's active version, then enqueues it. If enqueueing
     * fails the run stays QUEUED and the {@link dev.agentplatform.controlplane.queue.RunQueueException}
     * propagates (a later sweep re-enqueues such runs).
     */
    public Run start(String tenantId, String agentName, String inputJson) {
        var agent = agents.findAgent(tenantId, agentName)
                .orElseThrow(() -> new AgentNotFoundException(tenantId, agentName));
        if (agent.activeVersion() == null) {
            throw new AgentNotPublishedException(agentName);
        }
        var run = Run.queued(newRunId(), tenantId, agentName, agent.activeVersion(), inputJson, clock.instant());
        runs.create(run);
        queue.publish(new RunMessage(run.runId(), tenantId));
        return run;
    }

    /**
     * Starts a run of every agent of this tenant whose active version listens to {@code trigger}.
     * Agents of other tenants are never matched. Returns the started runs (none if no agent
     * matches). If enqueueing fails partway, the runs saved so far stay QUEUED and the
     * {@link dev.agentplatform.controlplane.queue.RunQueueException} propagates.
     */
    public List<Run> startForTrigger(String tenantId, String trigger, String inputJson) {
        return agents.findAgentsByTrigger(trigger).stream()
                .filter(agent -> agent.tenantId().equals(tenantId))
                .sorted(Comparator.comparing(Agent::name))
                .map(agent -> start(tenantId, agent.name(), inputJson))
                .toList();
    }

    /** A run as its tenant sees it; another tenant's run is reported as not found. */
    public Run get(String tenantId, String runId) {
        return runs.find(runId)
                .filter(run -> run.tenantId().equals(tenantId))
                .orElseThrow(() -> new RunNotFoundException(runId));
    }

    public RunWithSpec loadForWorker(String runId) {
        var run = runs.find(runId).orElseThrow(() -> new RunNotFoundException(runId));
        var version = agents.findVersion(run.tenantId(), run.agentName(), run.agentVersion())
                .orElseThrow(() -> new IllegalStateException(
                        "Run %s points at missing version %s v%d".formatted(runId, run.agentName(), run.agentVersion())));
        return new RunWithSpec(run, version.specJson());
    }

    public Run update(String runId, RunUpdate update) {
        return runs.update(runId, update, clock.instant());
    }

    static String newRunId() {
        var bytes = new byte[16];
        RANDOM.nextBytes(bytes);
        return "run_" + HexFormat.of().formatHex(bytes);
    }
}
