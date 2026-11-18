package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * Port for runs (ADR 0004, ADR 0008). Every adapter must pass {@code RunStoreContractTest}.
 * Behavior every adapter guarantees:
 *
 * <ul>
 *   <li>A run id is stored once.
 *   <li>A status change happens only from a status in {@code update.status().allowedFrom()}, checked
 *       atomically, so two concurrent changes cannot both succeed.
 *   <li>A QUEUED run has a queued-at time: its creation, its last return to QUEUED, or its last
 *       {@link #touchQueued}. Runs in other statuses are never listed as queued.
 *   <li>A pending call is decided at most once ({@link #decideApproval}).
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

    /**
     * QUEUED runs whose queued-at time is before {@code cutoff}, oldest first, at most
     * {@code limit}. May lag a just-made change (DynamoDB index); callers confirm with
     * {@link #touchQueued}.
     */
    List<Run> findQueuedBefore(Instant cutoff, int limit);

    /**
     * Sets the run's queued-at time to {@code now} if it is still QUEUED.
     *
     * @return false if the run is missing or no longer QUEUED
     */
    boolean touchQueued(String runId, Instant now);

    /**
     * Records a decision on the run's pending call, atomically and only if the run is still
     * WAITING_APPROVAL on exactly {@code pendingApprovalJson}: sets the approval, clears the
     * pending call, and moves the run to {@link ApprovalDecision#nextStatus()} (with the rejection
     * failure if rejected). An approved run becomes QUEUED with queued-at {@code now}.
     *
     * @throws RunNotFoundException if no run has this id
     * @throws IllegalTransitionException if the run is not WAITING_APPROVAL (for example, decided)
     * @throws ApprovalConflictException if the run waits on a different pending call
     */
    Run decideApproval(String runId, String pendingApprovalJson, ApprovalDecision decision, Instant now);
}
