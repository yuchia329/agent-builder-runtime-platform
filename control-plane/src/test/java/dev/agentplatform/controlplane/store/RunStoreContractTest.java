package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Behavior every {@link RunStore} adapter must have (ADR 0004). Each adapter gets a subclass that
 * supplies a fresh, empty store.
 */
abstract class RunStoreContractTest {

    private static final Instant T0 = Instant.parse("2026-11-16T18:00:00Z");
    private static final Instant T1 = T0.plus(1, ChronoUnit.MINUTES);
    private static final String INPUT = "{\"issue\":{\"number\":42}}";

    protected RunStore store;

    protected abstract RunStore newStore();

    @BeforeEach
    void setUp() {
        store = newStore();
    }

    private Run queuedRun() {
        return queuedRun(T0);
    }

    private Run queuedRun(Instant at) {
        var run = Run.queued("run_" + UUID.randomUUID().toString().replace("-", ""), "t1", "triage", 3, INPUT, at);
        store.create(run);
        return run;
    }

    private List<String> queuedBefore(Instant cutoff) {
        return store.findQueuedBefore(cutoff, 100).stream().map(Run::runId).toList();
    }

    private static RunUpdate to(RunStatus status) {
        return new RunUpdate(status, null, null, null, null, null, null);
    }

    @Test
    void createdRunIsFoundUnchanged() {
        var run = queuedRun();

        assertThat(store.find(run.runId())).contains(run);
    }

    @Test
    void missingRunIsEmpty() {
        assertThat(store.find("run_missing")).isEmpty();
    }

    @Test
    void runIdIsStoredOnce() {
        var run = queuedRun();

        assertThatThrownBy(() -> store.create(run)).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void startingARunRecordsProgress() {
        var run = queuedRun();

        var updated = store.update(run.runId(), new RunUpdate(RunStatus.RUNNING, 1, new BigDecimal("0.0012"),
                "4bf92f3577b34da6a3ce929d0e0e4736", null, null, null), T1);

        assertThat(updated.status()).isEqualTo(RunStatus.RUNNING);
        assertThat(updated.steps()).isEqualTo(1);
        assertThat(updated.costUsd()).isEqualByComparingTo("0.0012");
        assertThat(updated.traceId()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");
        assertThat(updated.updatedAt()).isEqualTo(T1);
        assertThat(updated.createdAt()).isEqualTo(T0);
        assertThat(updated.inputJson()).isEqualTo(INPUT);
        assertThat(store.find(run.runId())).contains(updated);
    }

    @Test
    void progressUpdateKeepsFieldsItDoesNotSend() {
        var run = queuedRun();
        store.update(run.runId(), new RunUpdate(RunStatus.RUNNING, 2, new BigDecimal("0.5"),
                "4bf92f3577b34da6a3ce929d0e0e4736", null, null, null), T1);

        var updated = store.update(run.runId(), new RunUpdate(RunStatus.RUNNING, 3, null, null, null, null, null), T1);

        assertThat(updated.steps()).isEqualTo(3);
        assertThat(updated.costUsd()).isEqualByComparingTo("0.5");
        assertThat(updated.traceId()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");
    }

    @Test
    void finishedRunsRecordTheirOutcome() {
        var waiting = queuedRun();
        var failed = queuedRun();
        var succeeded = queuedRun();
        for (var run : List.of(waiting, failed, succeeded)) {
            store.update(run.runId(), to(RunStatus.RUNNING), T1);
        }
        var pending = "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}";

        store.update(waiting.runId(), new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null, pending, null, null), T1);
        store.update(failed.runId(), new RunUpdate(RunStatus.FAILED, null, null, null, null,
                new Run.Failure("budget_steps", "20 steps used"), null), T1);
        store.update(succeeded.runId(), new RunUpdate(RunStatus.SUCCEEDED, null, null, null, null, null, "Labeled."), T1);

        assertThat(store.find(waiting.runId()).orElseThrow().pendingApprovalJson()).isEqualTo(pending);
        assertThat(store.find(failed.runId()).orElseThrow().failure())
                .isEqualTo(new Run.Failure("budget_steps", "20 steps used"));
        assertThat(store.find(succeeded.runId()).orElseThrow().output()).isEqualTo("Labeled.");
    }

    @Test
    void queuedRunCannotFinishWithoutRunning() {
        var run = queuedRun();

        assertThatThrownBy(() -> store.update(run.runId(), to(RunStatus.SUCCEEDED), T1))
                .isInstanceOfSatisfying(IllegalTransitionException.class, e -> {
                    assertThat(e.current()).isEqualTo(RunStatus.QUEUED);
                    assertThat(e.requested()).isEqualTo(RunStatus.SUCCEEDED);
                });
        assertThat(store.find(run.runId()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void finishedRunCannotChange() {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        store.update(run.runId(), to(RunStatus.SUCCEEDED), T1);

        assertThatThrownBy(() -> store.update(run.runId(), to(RunStatus.RUNNING), T1))
                .isInstanceOf(IllegalTransitionException.class);
        assertThatThrownBy(() -> store.update(run.runId(), to(RunStatus.SUCCEEDED), T1))
                .isInstanceOf(IllegalTransitionException.class);
    }

    @Test
    void queuedIsNeverAnUpdateTarget() {
        var run = queuedRun();

        assertThatThrownBy(() -> store.update(run.runId(), to(RunStatus.QUEUED), T1))
                .isInstanceOf(IllegalTransitionException.class);
    }

    @Test
    void updatingMissingRunFails() {
        assertThatThrownBy(() -> store.update("run_missing", to(RunStatus.RUNNING), T1))
                .isInstanceOf(RunNotFoundException.class);
    }

    @Test
    void concurrentFinishesHaveOneWinner() throws Exception {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        var start = new CountDownLatch(1);
        var tasks = new ArrayList<Callable<Boolean>>();
        for (var target : List.of(RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.SUCCEEDED, RunStatus.FAILED)) {
            var update = target == RunStatus.FAILED
                    ? new RunUpdate(target, null, null, null, null, new Run.Failure("error", "boom"), null)
                    : to(target);
            tasks.add(() -> {
                start.await();
                try {
                    store.update(run.runId(), update, T1);
                    return true;
                } catch (IllegalTransitionException e) {
                    return false;
                }
            });
        }

        var wins = 0;
        try (var pool = Executors.newFixedThreadPool(tasks.size())) {
            var futures = tasks.stream().map(pool::submit).toList();
            start.countDown();
            for (var future : futures) {
                wins += future.get() ? 1 : 0;
            }
        }

        assertThat(wins).isEqualTo(1);
        assertThat(store.find(run.runId()).orElseThrow().status()).isIn(RunStatus.SUCCEEDED, RunStatus.FAILED);
    }

    // Approval decisions

    private static final String PENDING =
            "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}";

    private Run waitingRun(String pending) {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        return store.update(run.runId(),
                new RunUpdate(RunStatus.WAITING_APPROVAL, 6, null, null, pending, null, null), T1);
    }

    private static ApprovalDecision decision(String pending, Decision decision) {
        return ApprovalDecision.of(pending, decision, "api:t1", T1);
    }

    @Test
    void approvalQueuesTheRunAndClearsThePendingCall() {
        var run = waitingRun(PENDING);
        var approved = decision(PENDING, Decision.approved);
        var at = T1.plusSeconds(5);

        var decided = store.decideApproval(run.runId(), PENDING, approved, at);

        assertThat(decided.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(decided.pendingApprovalJson()).isNull();
        assertThat(decided.approvalJson()).isEqualTo(approved.toJson());
        assertThat(decided.failure()).isNull();
        assertThat(decided.steps()).isEqualTo(6);
        assertThat(decided.updatedAt()).isEqualTo(at);
        assertThat(store.find(run.runId())).contains(decided);
        assertThat(queuedBefore(at.plusMillis(1))).containsExactly(run.runId()); // the sweep covers a lost enqueue
    }

    @Test
    void rejectionFailsTheRun() {
        var run = waitingRun(PENDING);
        var rejected = decision(PENDING, Decision.rejected);

        var decided = store.decideApproval(run.runId(), PENDING, rejected, T1);

        assertThat(decided.status()).isEqualTo(RunStatus.FAILED);
        assertThat(decided.pendingApprovalJson()).isNull();
        assertThat(decided.approvalJson()).isEqualTo(rejected.toJson());
        assertThat(decided.failure().reason()).isEqualTo("approval_rejected");
        assertThat(decided.failure().message()).contains("github.close_issue", "api:t1");
        assertThat(queuedBefore(T1.plusSeconds(60))).isEmpty();
    }

    @Test
    void aPendingCallIsDecidedOnce() {
        var run = waitingRun(PENDING);
        store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1);

        assertThatThrownBy(() -> store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.rejected), T1))
                .isInstanceOfSatisfying(IllegalTransitionException.class,
                        e -> assertThat(e.current()).isEqualTo(RunStatus.QUEUED));
        assertThat(store.find(run.runId()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void runNotWaitingCannotBeDecided() {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);

        assertThatThrownBy(() -> store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1))
                .isInstanceOfSatisfying(IllegalTransitionException.class,
                        e -> assertThat(e.current()).isEqualTo(RunStatus.RUNNING));
    }

    @Test
    void decisionOnAnOlderPendingCallConflicts() {
        var later = "{\"step\":9,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":43}}";
        var run = waitingRun(later);

        assertThatThrownBy(() -> store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1))
                .isInstanceOf(ApprovalConflictException.class);
        var unchanged = store.find(run.runId()).orElseThrow();
        assertThat(unchanged.status()).isEqualTo(RunStatus.WAITING_APPROVAL);
        assertThat(unchanged.pendingApprovalJson()).isEqualTo(later);
        assertThat(unchanged.approvalJson()).isNull();
    }

    @Test
    void decidingAMissingRunFails() {
        assertThatThrownBy(() -> store.decideApproval("run_missing", PENDING, decision(PENDING, Decision.approved), T1))
                .isInstanceOf(RunNotFoundException.class);
    }

    @Test
    void approvalStaysThroughTheNextPause() {
        var run = waitingRun(PENDING);
        var approved = decision(PENDING, Decision.approved);
        store.decideApproval(run.runId(), PENDING, approved, T1);
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        var next = "{\"step\":9,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":43}}";

        var paused = store.update(run.runId(),
                new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null, next, null, null), T1);

        assertThat(paused.pendingApprovalJson()).isEqualTo(next);
        assertThat(paused.approvalJson()).isEqualTo(approved.toJson()); // latest decision; its step is 7
    }

    @Test
    void concurrentDecisionsHaveOneWinner() throws Exception {
        var run = waitingRun(PENDING);
        var start = new CountDownLatch(1);
        var tasks = new ArrayList<Callable<Boolean>>();
        for (var choice : List.of(Decision.approved, Decision.rejected, Decision.approved, Decision.rejected)) {
            tasks.add(() -> {
                start.await();
                try {
                    store.decideApproval(run.runId(), PENDING, decision(PENDING, choice), T1);
                    return true;
                } catch (IllegalTransitionException e) {
                    return false;
                }
            });
        }

        var wins = 0;
        try (var pool = Executors.newFixedThreadPool(tasks.size())) {
            var futures = tasks.stream().map(pool::submit).toList();
            start.countDown();
            for (var future : futures) {
                wins += future.get() ? 1 : 0;
            }
        }

        assertThat(wins).isEqualTo(1);
    }

    // Queued runs (re-enqueue sweep)

    @Test
    void queuedRunIsListedOnceItsQueuedAtIsBeforeTheCutoff() {
        var run = queuedRun();

        assertThat(queuedBefore(T0)).isEmpty(); // strictly before
        assertThat(queuedBefore(T0.plusMillis(1))).containsExactly(run.runId());
        assertThat(store.findQueuedBefore(T1, 10).getFirst()).isEqualTo(run);
    }

    @Test
    void runThatLeftQueuedIsNotListedAndCannotBeTouched() {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);

        assertThat(queuedBefore(T1.plusSeconds(60))).isEmpty();
        assertThat(store.touchQueued(run.runId(), T1)).isFalse();
    }

    @Test
    void touchMovesQueuedAtForward() {
        var run = queuedRun();

        assertThat(store.touchQueued(run.runId(), T1)).isTrue();

        assertThat(queuedBefore(T1)).isEmpty();
        assertThat(queuedBefore(T1.plusMillis(1))).containsExactly(run.runId());
        assertThat(store.find(run.runId()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void touchOfAMissingRunIsFalse() {
        assertThat(store.touchQueued("run_missing", T1)).isFalse();
    }

    @Test
    void queuedRunsComeOldestFirstUpToTheLimit() {
        var later = queuedRun(T0.plusSeconds(1));
        var first = queuedRun(T0);
        var subSecond = queuedRun(T0.plusMillis(500)); // fractional seconds must sort by time
        queuedRun(T0.plusSeconds(2));

        var listed = store.findQueuedBefore(T1, 3).stream().map(Run::runId).toList();

        assertThat(listed).containsExactly(first.runId(), subSecond.runId(), later.runId());
    }
}
