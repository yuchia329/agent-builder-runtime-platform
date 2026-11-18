package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.run.RunStatus;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;

/** {@link RunStore} for tests and local runs without DynamoDB. Thread-safe; data is lost on exit. */
public class InMemoryRunStore implements RunStore {

    private final Map<String, Run> runs = new HashMap<>();
    private final Map<String, Instant> queuedAt = new HashMap<>(); // QUEUED runs only

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
    public synchronized Run update(String runId, RunUpdate update, Instant now) {
        var current = runs.get(runId);
        if (current == null) {
            throw new RunNotFoundException(runId);
        }
        if (!update.status().allowedFrom().contains(current.status())) {
            throw new IllegalTransitionException(runId, current.status(), update.status());
        }
        var updated = current.with(update, now);
        runs.put(runId, updated);
        if (updated.status() == RunStatus.QUEUED) {
            queuedAt.put(runId, now);
        } else {
            queuedAt.remove(runId);
        }
        return updated;
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
        if (updated.status() == RunStatus.QUEUED) {
            queuedAt.put(runId, now);
        }
        return updated;
    }
}
