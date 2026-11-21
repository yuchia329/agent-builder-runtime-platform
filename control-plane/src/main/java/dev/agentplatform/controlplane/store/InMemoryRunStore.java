package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.run.RunStatus;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

/** {@link RunStore} for tests and local runs without DynamoDB. Thread-safe; data is lost on exit. */
public class InMemoryRunStore implements RunStore {

    private final Map<String, Run> runs = new HashMap<>();
    private final Map<String, Instant> queuedAt = new HashMap<>(); // QUEUED runs only
    private final Map<String, Instant> unsentSince = new HashMap<>(); // WAITING_APPROVAL, not posted
    private final Map<String, Instant> waitingSince = new HashMap<>(); // WAITING_APPROVAL, posted or not
    private final Map<String, Instant> runningSince = new HashMap<>(); // RUNNING: last update
    private final Set<String> holdingSlot = new HashSet<>();
    private final Map<String, Integer> running = new HashMap<>(); // tenant -> slots held
    private final Map<String, BigDecimal> spend = new HashMap<>(); // tenant#day -> USD

    @Override
    public synchronized void create(Run run) {
        if (runs.putIfAbsent(run.runId(), run) != null) {
            throw new IllegalStateException("Run '%s' already exists".formatted(run.runId()));
        }
        if (run.status() == RunStatus.QUEUED) {
            queuedAt.put(run.runId(), run.createdAt());
        }
    }

    @Override
    public synchronized Optional<Run> find(String runId) {
        return Optional.ofNullable(runs.get(runId));
    }

    @Override
    public synchronized Run update(String runId, RunUpdate update, Instant now, int maxRunning) {
        var current = runs.get(runId);
        if (current == null) {
            throw new RunNotFoundException(runId);
        }
        if (!update.status().allowedFrom().contains(current.status())) {
            throw new IllegalTransitionException(runId, current.status(), update.status());
        }
        var take = current.status() == RunStatus.QUEUED && update.status() == RunStatus.RUNNING;
        if (take && running.getOrDefault(current.tenantId(), 0) >= maxRunning) {
            throw new TenantAtCapException(current.tenantId(), maxRunning);
        }
        var updated = current.with(update, now);
        runs.put(runId, updated);
        index(updated, now);
        if (take) {
            holdingSlot.add(runId);
            running.merge(current.tenantId(), 1, Integer::sum);
        }
        releaseUnlessRunning(updated);
        var increase = updated.costUsd().subtract(current.costUsd());
        if (increase.signum() > 0) {
            spend.merge(spendKey(current.tenantId(), now), increase, BigDecimal::add);
        }
        return updated;
    }

    @Override
    public synchronized Run setAside(String runId, int delaySeconds, Instant dueAt, Instant now) {
        var current = runs.get(runId);
        if (current == null) {
            throw new RunNotFoundException(runId);
        }
        if (current.status() != RunStatus.RUNNING) {
            throw new IllegalTransitionException(runId, current.status(), RunStatus.QUEUED);
        }
        var queued = current.setAside(delaySeconds, now);
        runs.put(runId, queued);
        index(queued, dueAt);
        releaseUnlessRunning(queued);
        return queued;
    }

    @Override
    public synchronized Optional<Run> failQueued(String runId, Run.Failure failure, Instant now) {
        var current = runs.get(runId);
        if (current == null || current.status() != RunStatus.QUEUED) {
            return Optional.empty();
        }
        var failed = current.with(new RunUpdate(RunStatus.FAILED, null, null, null, null, failure, null), now);
        runs.put(runId, failed);
        index(failed, now);
        return Optional.of(failed);
    }

    @Override
    public synchronized List<Run> findRunningBefore(Instant cutoff, int limit) {
        return before(runningSince, cutoff, limit);
    }

    @Override
    public synchronized Optional<Run> failStalled(String runId, Instant lastUpdatedAt, Run.Failure failure,
                                                  Instant now) {
        var current = runs.get(runId);
        if (current == null || current.status() != RunStatus.RUNNING || !current.updatedAt().equals(lastUpdatedAt)) {
            return Optional.empty();
        }
        var failed = current.with(new RunUpdate(RunStatus.FAILED, null, null, null, null, failure, null), now);
        runs.put(runId, failed);
        index(failed, now);
        releaseUnlessRunning(failed);
        return Optional.of(failed);
    }

    @Override
    public synchronized int running(String tenantId) {
        return running.getOrDefault(tenantId, 0);
    }

    @Override
    public synchronized BigDecimal spend(String tenantId, LocalDate day) {
        return spend.getOrDefault(tenantId + "#" + day, BigDecimal.ZERO);
    }

    private static String spendKey(String tenantId, Instant at) {
        return tenantId + "#" + LocalDate.ofInstant(at, ZoneOffset.UTC);
    }

    private void releaseUnlessRunning(Run run) {
        if (run.status() != RunStatus.RUNNING && holdingSlot.remove(run.runId())) {
            running.merge(run.tenantId(), -1, Integer::sum);
        }
    }

    /** The run's place in the status lists after a status change at {@code at}. */
    private void index(Run run, Instant at) {
        var id = run.runId();
        if (run.status() == RunStatus.QUEUED) {
            queuedAt.put(id, at);
        } else {
            queuedAt.remove(id);
        }
        if (run.status() == RunStatus.WAITING_APPROVAL) {
            unsentSince.put(id, at);
            waitingSince.put(id, at);
        } else {
            unsentSince.remove(id);
            waitingSince.remove(id);
        }
        if (run.status() == RunStatus.RUNNING) {
            runningSince.put(id, at);
        } else {
            runningSince.remove(id);
        }
    }

    @Override
    public synchronized List<Run> findQueuedBefore(Instant cutoff, int limit) {
        return queuedAt.entrySet().stream()
                .filter(e -> e.getValue().isBefore(cutoff))
                .sorted(Map.Entry.<String, Instant>comparingByValue().thenComparing(Map.Entry.comparingByKey()))
                .limit(limit)
                .map(e -> runs.get(e.getKey()))
                .toList();
    }

    @Override
    public synchronized boolean touchQueued(String runId, Instant now) {
        if (!queuedAt.containsKey(runId)) {
            return false;
        }
        queuedAt.put(runId, now);
        return true;
    }

    @Override
    public synchronized Run decideApproval(String runId, String pendingApprovalJson, ApprovalDecision decision,
                                           Instant now) {
        var current = runs.get(runId);
        if (current == null) {
            throw new RunNotFoundException(runId);
        }
        if (current.status() != RunStatus.WAITING_APPROVAL) {
            throw new IllegalTransitionException(runId, current.status(), decision.nextStatus());
        }
        if (!Objects.equals(current.pendingApprovalJson(), pendingApprovalJson)) {
            throw new ApprovalConflictException("Run '%s' is waiting on a different call".formatted(runId));
        }
        var updated = current.decided(decision, now);
        runs.put(runId, updated);
        unsentSince.remove(runId);
        waitingSince.remove(runId);
        if (updated.status() == RunStatus.QUEUED) {
            queuedAt.put(runId, now);
        }
        return updated;
    }

    @Override
    public synchronized List<Run> findUnsentApprovalsBefore(Instant cutoff, int limit) {
        return before(unsentSince, cutoff, limit);
    }

    @Override
    public synchronized List<Run> findWaitingApprovalsBefore(Instant cutoff, int limit) {
        return before(waitingSince, cutoff, limit);
    }

    @Override
    public synchronized Optional<Run> expireApproval(String runId, String pendingApprovalJson, Run.Failure failure,
                                                     Instant now) {
        var current = runs.get(runId);
        if (current == null || current.status() != RunStatus.WAITING_APPROVAL
                || !Objects.equals(current.pendingApprovalJson(), pendingApprovalJson)) {
            return Optional.empty();
        }
        var expired = current.expired(failure, now);
        runs.put(runId, expired);
        unsentSince.remove(runId);
        waitingSince.remove(runId);
        return Optional.of(expired);
    }

    private List<Run> before(Map<String, Instant> since, Instant cutoff, int limit) {
        return since.entrySet().stream()
                .filter(e -> e.getValue().isBefore(cutoff))
                .sorted(Map.Entry.<String, Instant>comparingByValue().thenComparing(Map.Entry.comparingByKey()))
                .limit(limit)
                .map(e -> runs.get(e.getKey()))
                .toList();
    }

    @Override
    public synchronized boolean markApprovalSent(String runId, String pendingApprovalJson, ApprovalMessage message) {
        var current = runs.get(runId);
        if (current == null || current.status() != RunStatus.WAITING_APPROVAL
                || !Objects.equals(current.pendingApprovalJson(), pendingApprovalJson)
                || current.approvalMessage() != null) {
            return false;
        }
        runs.put(runId, current.withApprovalMessage(message));
        unsentSince.remove(runId);
        return true;
    }
}
