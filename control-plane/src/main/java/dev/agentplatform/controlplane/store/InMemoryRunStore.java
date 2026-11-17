package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/** {@link RunStore} for tests and local runs without DynamoDB. Thread-safe; data is lost on exit. */
public class InMemoryRunStore implements RunStore {

    private final Map<String, Run> runs = new HashMap<>();

    @Override
    public synchronized void create(Run run) {
        if (runs.putIfAbsent(run.runId(), run) != null) {
            throw new IllegalStateException("Run '%s' already exists".formatted(run.runId()));
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
        return updated;
    }
}
