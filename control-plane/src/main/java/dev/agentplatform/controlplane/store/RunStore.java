package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.time.Instant;
import java.util.Optional;

/**
 * Port for runs (ADR 0004, ADR 0008). Every adapter must pass {@code RunStoreContractTest}.
 * Behavior every adapter guarantees:
 *
 * <ul>
 *   <li>A run id is stored once.
 *   <li>A status change happens only from a status in {@code update.status().allowedFrom()}, checked
 *       atomically, so two concurrent changes cannot both succeed.
 * </ul>
 */
public interface RunStore {

    /** @throws IllegalStateException if a run with this id exists */
    void create(Run run);

    Optional<Run> find(String runId);

    /**
     * Applies the update if the run's current status allows it, and returns the updated run.
     *
     * @throws RunNotFoundException if no run has this id
     * @throws IllegalTransitionException if the current status does not allow the change
     */
    Run update(String runId, RunUpdate update, Instant now);
}
