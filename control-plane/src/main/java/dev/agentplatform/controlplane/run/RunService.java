package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.approval.ApprovalNotifier;
import dev.agentplatform.controlplane.queue.RunMessage;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.AgentNotFoundException;
import dev.agentplatform.controlplane.store.AgentStore;
import dev.agentplatform.controlplane.store.ApprovalConflictException;
import dev.agentplatform.controlplane.store.IllegalTransitionException;
import dev.agentplatform.controlplane.store.RunNotFoundException;
import dev.agentplatform.controlplane.store.RunStore;
import dev.agentplatform.controlplane.agent.Agent;
import java.time.Clock;
import java.util.Comparator;
import java.util.List;
import java.util.HexFormat;
import java.security.SecureRandom;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;

@Service
public class RunService {

    private static final Logger log = LoggerFactory.getLogger(RunService.class);
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final AgentStore agents;
    private final RunStore runs;
    private final RunQueuePublisher queue;
    private final ApprovalNotifier approvals;
    private final Clock clock;

    public RunService(AgentStore agents, RunStore runs, RunQueuePublisher queue, ApprovalNotifier approvals,
                      Clock clock) {
        this.agents = agents;
        this.runs = runs;
        this.queue = queue;
        this.approvals = approvals;
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

    /**
     * Applies a worker's report. A move to WAITING_APPROVAL also posts the pending call for a
     * person (ADR 0002); a failed post does not fail the report (the approval sweep retries).
     */
    public Run update(String runId, RunUpdate update) {
        var updated = runs.update(runId, update, clock.instant());
        if (updated.status() == RunStatus.WAITING_APPROVAL
                && approvals.post(updated) == ApprovalNotifier.Outcome.POSTED) {
            return runs.find(runId).orElse(updated);
        }
        return updated;
    }

    /**
     * Records a person's decision on the run's pending call (ADR 0002). {@code step} must be the
     * pending call's step, so a stale or repeated request cannot decide a later pause. Approved:
     * the run becomes QUEUED and is enqueued; if enqueueing fails the decision still stands and
     * the re-enqueue sweep sends the run. Rejected: the run fails with {@code approval_rejected}.
     *
     * @throws RunNotFoundException if the tenant has no such run
     * @throws IllegalTransitionException if the run is not waiting for approval (or was decided)
     * @throws ApprovalConflictException if the run waits on a different step
     */
    public Run decideApproval(String tenantId, String runId, int step, ApprovalDecision.Decision decision,
                              String decidedBy) {
        var run = get(tenantId, runId);
        var pendingJson = run.pendingApprovalJson();
        if (run.status() != RunStatus.WAITING_APPROVAL || pendingJson == null) {
            throw new IllegalTransitionException(runId, run.status(),
                    decision == ApprovalDecision.Decision.approved ? RunStatus.QUEUED : RunStatus.FAILED);
        }
        var pendingStep = JSON.readTree(pendingJson).get("step").asInt();
        if (pendingStep != step) {
            throw new ApprovalConflictException(
                    "Run '%s' is waiting on step %d, not step %d".formatted(runId, pendingStep, step));
        }
        var now = clock.instant();
        var decided = runs.decideApproval(runId, pendingJson,
                ApprovalDecision.of(pendingJson, decision, decidedBy, now), now);
        if (decided.status() == RunStatus.QUEUED) {
            try {
                queue.publish(new RunMessage(runId, decided.tenantId()));
            } catch (RunQueueException e) {
                log.warn("Run {} approved and QUEUED but not enqueued; the sweep will send it", runId, e);
            }
        }
        return decided;
    }

    static String newRunId() {
        var bytes = new byte[16];
        RANDOM.nextBytes(bytes);
        return "run_" + HexFormat.of().formatHex(bytes);
    }
}
