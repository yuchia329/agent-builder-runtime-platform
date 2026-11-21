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
import dev.agentplatform.controlplane.store.TenantAtCapException;
import dev.agentplatform.controlplane.tenant.Principals;
import dev.agentplatform.controlplane.trace.RunTracing;
import dev.agentplatform.controlplane.agent.Agent;
import java.time.Clock;
import java.util.Comparator;
import java.util.List;
import java.util.HexFormat;
import java.security.SecureRandom;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
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
    private final RunTracing tracing;
    private final RunLimits limits;

    @Autowired
    public RunService(AgentStore agents, RunStore runs, RunQueuePublisher queue, ApprovalNotifier approvals,
                      Clock clock, RunTracing tracing, RunLimits limits) {
        this.agents = agents;
        this.runs = runs;
        this.queue = queue;
        this.approvals = approvals;
        this.clock = clock;
        this.tracing = tracing;
        this.limits = limits;
    }

    /** With these limits and no tracing. */
    public RunService(AgentStore agents, RunStore runs, RunQueuePublisher queue, ApprovalNotifier approvals,
                      Clock clock, RunLimits limits) {
        this(agents, runs, queue, approvals, clock, RunTracing.noop(), limits);
    }

    /** Without tracing or limits (tests that look at neither). */
    public RunService(AgentStore agents, RunStore runs, RunQueuePublisher queue, ApprovalNotifier approvals,
                      Clock clock) {
        this(agents, runs, queue, approvals, clock, RunTracing.noop(), RunLimits.unlimited(runs, clock));
    }

    /** A run plus the spec and publisher ({@code owner}) of the version it is pinned to, for workers. */
    public record RunWithSpec(Run run, String specJson, String owner) {}

    /**
     * Stores a QUEUED run pinned to the agent's active version, then enqueues it, in a new trace
     * (its "run.start" span, ADR 0022) that the queue message carries to the worker. If enqueueing
     * fails the run stays QUEUED and the {@link dev.agentplatform.controlplane.queue.RunQueueException}
     * propagates (a later sweep re-enqueues such runs).
     *
     * @throws TenantBudgetExceededException if the tenant has reached its daily spend cap
     */
    public Run start(String tenantId, String agentName, String inputJson) {
        return start(tenantId, agentName, inputJson, Principals.api(tenantId));
    }

    /** {@link #start(String, String, String)} recording what started the run ({@code startedBy}). */
    public Run start(String tenantId, String agentName, String inputJson, String startedBy) {
        var agent = agents.findAgent(tenantId, agentName)
                .orElseThrow(() -> new AgentNotFoundException(tenantId, agentName));
        if (agent.activeVersion() == null) {
            throw new AgentNotPublishedException(agentName);
        }
        limits.requireUnderDailyCap(tenantId);
        var runId = newRunId();
        var span = tracing.startRun(runId, tenantId, agentName, agent.activeVersion());
        return RunTracing.inSpan(span, () -> {
            var run = Run.queued(runId, tenantId, agentName, agent.activeVersion(), inputJson, startedBy,
                    RunTracing.traceParent(span), clock.instant());
            runs.create(run);
            queue.publish(new RunMessage(run.runId(), tenantId, run.traceParent()));
            return run;
        });
    }

    /**
     * Starts a run of every agent of this tenant whose active version listens to {@code trigger}.
     * Agents of other tenants are never matched. Returns the started runs (none if no agent
     * matches). If enqueueing fails partway, the runs saved so far stay QUEUED and the
     * {@link dev.agentplatform.controlplane.queue.RunQueueException} propagates.
     */
    public List<Run> startForTrigger(String tenantId, String trigger, String inputJson, String startedBy) {
        return agents.findAgentsByTrigger(trigger).stream()
                .filter(agent -> agent.tenantId().equals(tenantId))
                .sorted(Comparator.comparing(Agent::name))
                .map(agent -> start(tenantId, agent.name(), inputJson, startedBy))
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
        return new RunWithSpec(run, version.specJson(), version.owner());
    }

    /**
     * Applies a worker's report. A move to WAITING_APPROVAL also posts the pending call for a
     * person (ADR 0002); a failed post does not fail the report (the approval sweep retries).
     *
     * <p>A start (QUEUED → RUNNING) is where tenant limits apply (ADR 0026): over the daily spend
     * cap the run fails {@code tenant_budget} and this throws {@link IllegalTransitionException}
     * (409); at the cap on runs at once the run stays QUEUED, is re-queued with a delay and this
     * throws {@link RunDeferredException} (429).
     */
    public Run update(String runId, RunUpdate update) {
        if (update.status() == RunStatus.RUNNING) {
            var current = runs.find(runId).orElseThrow(() -> new RunNotFoundException(runId));
            if (current.status() == RunStatus.QUEUED) {
                return start(current, update);
            }
        }
        var updated = runs.update(runId, update, clock.instant());
        if (updated.status() == RunStatus.WAITING_APPROVAL
                && approvals.post(updated) == ApprovalNotifier.Outcome.POSTED) {
            return runs.find(runId).orElse(updated);
        }
        return updated;
    }

    private Run start(Run queued, RunUpdate update) {
        var runId = queued.runId();
        var tenantLimits = limits.of(queued.tenantId());
        var now = clock.instant();
        if (limits.overDailyCap(queued.tenantId(), tenantLimits)) {
            var failed = runs.failQueued(runId, new Run.Failure(TenantBudgetExceededException.REASON,
                    "Tenant reached its daily spend cap of $" + tenantLimits.dailyUsd().toPlainString()), now);
            if (failed.isPresent()) {
                throw new IllegalTransitionException(runId, RunStatus.FAILED, RunStatus.RUNNING);
            }
            // no longer QUEUED: another delivery started it; carry on as a resume
        }
        try {
            return runs.update(runId, update, now, tenantLimits.maxRunning());
        } catch (TenantAtCapException e) {
            var delay = limits.capRetryDelaySeconds();
            if (runs.touchQueued(runId, now.plusSeconds(delay))) {
                requeue(queued, delay, "run.deferred");
            }
            throw new RunDeferredException(runId, delay);
        }
    }

    /**
     * Sets a RUNNING run aside (ADR 0027): back to QUEUED, its slot freed, re-queued to arrive
     * after {@code delaySeconds}. A run already set aside {@value RunLimits#MAX_SET_ASIDE_SECONDS} s
     * in total fails {@code rate_limited} instead.
     *
     * @throws RunNotFoundException if no run has this id
     * @throws IllegalTransitionException if the run is not RUNNING
     */
    public Run defer(String runId, int delaySeconds) {
        var run = runs.find(runId).orElseThrow(() -> new RunNotFoundException(runId));
        if (run.status() != RunStatus.RUNNING) {
            throw new IllegalTransitionException(runId, run.status(), RunStatus.QUEUED);
        }
        var now = clock.instant();
        if (run.setAsideSeconds() >= RunLimits.MAX_SET_ASIDE_SECONDS) {
            return runs.update(runId, new RunUpdate(RunStatus.FAILED, null, null, null, null,
                    new Run.Failure("rate_limited", "Set aside for %d s in total: the model provider kept answering 429"
                            .formatted(run.setAsideSeconds())), null), now);
        }
        var queued = runs.setAside(runId, delaySeconds, now.plusSeconds(delaySeconds), now);
        requeue(queued, delaySeconds, "run.set_aside");
        return queued;
    }

    /** Sends a QUEUED run's message with a delay; on failure the re-enqueue sweep sends it later. */
    private void requeue(Run run, int delaySeconds, String spanName) {
        var span = tracing.startInRun(run, spanName);
        span.setAttribute("queue.delay_seconds", (long) delaySeconds);
        try {
            RunTracing.inSpan(span, () -> {
                queue.publish(new RunMessage(run.runId(), run.tenantId(), RunTracing.traceParent(span)), delaySeconds);
                return null;
            });
        } catch (RunQueueException e) {
            log.warn("Run {} re-queue failed; the sweep will send it: {}", run.runId(), e.getMessage());
        }
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
        var span = tracing.startInRun(run, "run.approval");
        span.setAttribute("approval.decision", decision.name());
        span.setAttribute("approval.step", (long) step);
        return RunTracing.inSpan(span, () -> {
            var now = clock.instant();
            var decided = runs.decideApproval(runId, pendingJson,
                    ApprovalDecision.of(pendingJson, decision, decidedBy, now), now);
            if (decided.status() == RunStatus.QUEUED) {
                try {
                    queue.publish(new RunMessage(runId, decided.tenantId(), RunTracing.traceParent(span)));
                } catch (RunQueueException e) {
                    log.warn("Run {} approved and QUEUED but not enqueued; the sweep will send it", runId, e);
                }
            }
            return decided;
        });
    }

    static String newRunId() {
        var bytes = new byte[16];
        RANDOM.nextBytes(bytes);
        return "run_" + HexFormat.of().formatHex(bytes);
    }
}
