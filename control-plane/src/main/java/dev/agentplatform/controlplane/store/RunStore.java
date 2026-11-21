package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
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
 *   <li>A WAITING_APPROVAL run has a waiting-since time (its last move to WAITING_APPROVAL) and is
 *       listed as unsent until {@link #markApprovalSent} records where its call was posted. Posted
 *       or not, it is listed by {@link #findWaitingApprovalsBefore} until it leaves WAITING_APPROVAL.
 *   <li>Slots (ADR 0026): a run takes one of its tenant's slots when it moves QUEUED → RUNNING and
 *       frees it when it leaves RUNNING, in the same atomic write as the status change, so
 *       {@link #running} always equals the tenant's RUNNING runs that took a slot. A RUNNING →
 *       RUNNING update takes no new slot.
 *   <li>Spend: an update that raises a run's {@code costUsd} adds the increase to its tenant's
 *       {@link #spend} for the UTC day of the update (best effort: a failed add is lost, never
 *       retried into a double count).
 *   <li>A RUNNING run is listed by {@link #findRunningBefore} by its last update time.
 * </ul>
 */
public interface RunStore {

    /** @throws IllegalStateException if a run with this id exists */
    void create(Run run);

    Optional<Run> find(String runId);

    /** {@link #update(String, RunUpdate, Instant, int)} with no cap on the tenant's runs at once. */
    default Run update(String runId, RunUpdate update, Instant now) {
        return update(runId, update, now, Integer.MAX_VALUE);
    }

    /**
     * Applies the update if the run's current status allows it, and returns the updated run. A
     * QUEUED → RUNNING change takes a slot only if the tenant has fewer than {@code maxRunning}
     * runs holding one.
     *
     * @throws RunNotFoundException if no run has this id
     * @throws IllegalTransitionException if the current status does not allow the change
     * @throws TenantAtCapException if the run would start but its tenant is at {@code maxRunning};
     *     the run is unchanged
     */
    Run update(String runId, RunUpdate update, Instant now, int maxRunning);

    /**
     * Sets a RUNNING run aside (ADR 0027): QUEUED with queued-at {@code dueAt} (when its delayed
     * message arrives), its slot freed, {@code delaySeconds} added to its set-aside total.
     *
     * @throws RunNotFoundException if no run has this id
     * @throws IllegalTransitionException if the run is not RUNNING
     */
    Run setAside(String runId, int delaySeconds, Instant dueAt, Instant now);

    /**
     * Fails a QUEUED run with {@code failure} (a start refused: {@code tenant_budget}).
     *
     * @return the failed run, or empty if the run is missing or no longer QUEUED
     */
    Optional<Run> failQueued(String runId, Run.Failure failure, Instant now);

    /**
     * RUNNING runs last updated before {@code cutoff}, oldest first, at most {@code limit}. May
     * lag a just-made change (DynamoDB index); {@link #failStalled} confirms.
     */
    List<Run> findRunningBefore(Instant cutoff, int limit);

    /**
     * Fails a RUNNING run with {@code failure} and frees its slot, only if its last update is
     * still {@code lastUpdatedAt} (it made no progress since it was listed).
     *
     * @return the failed run, or empty if the run is missing, not RUNNING or updated since
     */
    Optional<Run> failStalled(String runId, Instant lastUpdatedAt, Run.Failure failure, Instant now);

    /** Slots the tenant's runs hold now (0 for a tenant without runs). */
    int running(String tenantId);

    /** The tenant's model spend counted on {@code day} (UTC), 0 if none. */
    BigDecimal spend(String tenantId, LocalDate day);

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

    /**
     * WAITING_APPROVAL runs whose pending call was not posted yet and that started waiting before
     * {@code cutoff}, oldest first, at most {@code limit}. May lag a just-made change (DynamoDB
     * index); {@link #markApprovalSent} confirms.
     */
    List<Run> findUnsentApprovalsBefore(Instant cutoff, int limit);

    /**
     * Records where the pending call was posted, only if the run still waits on exactly
     * {@code pendingApprovalJson} and has no approval message yet.
     *
     * @return false if the run is missing, no longer waits on that call, or was already posted
     */
    boolean markApprovalSent(String runId, String pendingApprovalJson, ApprovalMessage message);

    /**
     * WAITING_APPROVAL runs, posted or not, that started waiting before {@code cutoff}, oldest
     * first, at most {@code limit}. May lag a just-made change (DynamoDB index);
     * {@link #expireApproval} confirms.
     */
    List<Run> findWaitingApprovalsBefore(Instant cutoff, int limit);

    /**
     * Fails the run with {@code failure}, atomically and only if it is still WAITING_APPROVAL on
     * exactly {@code pendingApprovalJson}: clears the pending call, keeps the latest approval and
     * the approval message.
     *
     * @return the failed run, or empty if the run is missing or no longer waits on that call (a
     *     decision won)
     */
    Optional<Run> expireApproval(String runId, String pendingApprovalJson, Run.Failure failure, Instant now);
}
