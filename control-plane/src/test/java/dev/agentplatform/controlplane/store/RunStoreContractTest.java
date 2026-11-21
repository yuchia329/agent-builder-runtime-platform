package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
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
    void runRecordsWhatStartedIt() {
        var run = Run.queued("run_" + UUID.randomUUID(), "t1", "triage", 1, INPUT, "webhook:github", T0);
        store.create(run);
        store.update(run.runId(), to(RunStatus.RUNNING), T1);

        assertThat(store.find(run.runId()).orElseThrow().startedBy()).isEqualTo("webhook:github");
        assertThat(queuedRun().startedBy()).isEqualTo("api:t1");
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
    void aRunsOwnTraceIsKeptAndAWorkersTraceIdDoesNotReplaceIt() {
        var traceParent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
        var run = Run.queued("run_traced", "tenant-demo", "issue-triage", 1, INPUT, "api:tenant-demo", traceParent, T0);
        store.create(run);
        assertThat(store.find(run.runId())).get().satisfies(found -> {
            assertThat(found.traceParent()).isEqualTo(traceParent);
            assertThat(found.traceId()).isEqualTo("0af7651916cd43dd8448eb211c80319c");
        });

        var updated = store.update(run.runId(), new RunUpdate(RunStatus.RUNNING, 1, null,
                "4bf92f3577b34da6a3ce929d0e0e4736", null, null, null), T1);

        assertThat(updated.traceId()).isEqualTo("0af7651916cd43dd8448eb211c80319c");
        assertThat(updated.traceParent()).isEqualTo(traceParent);
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

    // Approval posts (approval sweep)

    private static final ApprovalMessage POSTED = new ApprovalMessage("C0123456789", "1794980400.000100");

    private List<String> unsentBefore(Instant cutoff) {
        return store.findUnsentApprovalsBefore(cutoff, 100).stream().map(Run::runId).toList();
    }

    @Test
    void waitingRunIsUnsentUntilItsCallIsPosted() {
        var run = waitingRun(PENDING); // waiting since T1

        assertThat(unsentBefore(T1)).isEmpty(); // strictly before
        assertThat(unsentBefore(T1.plusMillis(1))).containsExactly(run.runId());
        assertThat(store.findUnsentApprovalsBefore(T1.plusMillis(1), 10).getFirst()).isEqualTo(run);

        assertThat(store.markApprovalSent(run.runId(), PENDING, POSTED)).isTrue();

        assertThat(store.find(run.runId()).orElseThrow().approvalMessage()).isEqualTo(POSTED);
        assertThat(unsentBefore(T1.plusSeconds(60))).isEmpty();
    }

    @Test
    void aPendingCallIsMarkedSentOnce() {
        var run = waitingRun(PENDING);
        store.markApprovalSent(run.runId(), PENDING, POSTED);

        var second = new ApprovalMessage("C0123456789", "1794980401.000200");
        assertThat(store.markApprovalSent(run.runId(), PENDING, second)).isFalse();
        assertThat(store.find(run.runId()).orElseThrow().approvalMessage()).isEqualTo(POSTED);
    }

    @Test
    void markingAnotherCallADecidedRunOrAMissingRunIsFalse() {
        var other = "{\"step\":9,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":43}}";
        var run = waitingRun(PENDING);

        assertThat(store.markApprovalSent(run.runId(), other, POSTED)).isFalse();
        store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.rejected), T1);
        assertThat(store.markApprovalSent(run.runId(), PENDING, POSTED)).isFalse();
        assertThat(store.find(run.runId()).orElseThrow().approvalMessage()).isNull();
        assertThat(store.markApprovalSent("run_missing", PENDING, POSTED)).isFalse();
    }

    @Test
    void decidedRunsAreNotUnsent() {
        var approved = waitingRun(PENDING);
        var rejected = waitingRun(PENDING);

        store.decideApproval(approved.runId(), PENDING, decision(PENDING, Decision.approved), T1);
        store.decideApproval(rejected.runId(), PENDING, decision(PENDING, Decision.rejected), T1);

        assertThat(unsentBefore(T1.plusSeconds(60))).isEmpty();
        assertThat(queuedBefore(T1.plusSeconds(60))).containsExactly(approved.runId());
    }

    @Test
    void decisionKeepsTheMessageAndANewPauseStartsUnsent() {
        var run = waitingRun(PENDING);
        store.markApprovalSent(run.runId(), PENDING, POSTED);
        var decided = store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1);
        assertThat(decided.approvalMessage()).isEqualTo(POSTED); // the decision can update the message
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        var next = "{\"step\":9,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":43}}";
        var later = T1.plusSeconds(30);

        var paused = store.update(run.runId(),
                new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null, next, null, null), later);

        assertThat(paused.approvalMessage()).isNull();
        assertThat(store.find(run.runId()).orElseThrow().approvalMessage()).isNull();
        assertThat(unsentBefore(later)).isEmpty(); // waiting since the new pause
        assertThat(unsentBefore(later.plusMillis(1))).containsExactly(run.runId());
    }

    private Run waitingSince(Instant at) {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), at);
        return store.update(run.runId(), new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null, PENDING, null, null), at);
    }

    @Test
    void unsentRunsComeOldestFirstUpToTheLimit() {
        var later = waitingSince(T1.plusSeconds(1));
        var first = waitingSince(T1);
        var subSecond = waitingSince(T1.plusMillis(500)); // fractional seconds must sort by time
        waitingSince(T1.plusSeconds(2));

        var listed = store.findUnsentApprovalsBefore(T1.plusSeconds(60), 3).stream().map(Run::runId).toList();

        assertThat(listed).containsExactly(first.runId(), subSecond.runId(), later.runId());
    }

    // Approval expiry

    private static final Run.Failure EXPIRED = new Run.Failure(ApprovalDecision.EXPIRED_REASON, "no decision in 72 h");

    private List<String> waitingBefore(Instant cutoff) {
        return store.findWaitingApprovalsBefore(cutoff, 100).stream().map(Run::runId).toList();
    }

    @Test
    void waitingRunsAreListedPostedOrNotOldestFirstUpToTheLimit() {
        var posted = waitingSince(T1);
        store.markApprovalSent(posted.runId(), PENDING, POSTED);
        var unsent = waitingSince(T1.plusMillis(500));
        var later = waitingSince(T1.plusSeconds(1));
        waitingSince(T1.plusSeconds(2));

        var listed = store.findWaitingApprovalsBefore(T1.plusSeconds(60), 3).stream().map(Run::runId).toList();

        assertThat(listed).containsExactly(posted.runId(), unsent.runId(), later.runId());
        assertThat(waitingBefore(T1)).isEmpty(); // strictly before the cutoff
    }

    @Test
    void expiryFailsTheRunAndKeepsItsMessage() {
        var run = waitingRun(PENDING);
        store.markApprovalSent(run.runId(), PENDING, POSTED);
        var at = T1.plusSeconds(10);

        var expired = store.expireApproval(run.runId(), PENDING, EXPIRED, at).orElseThrow();

        assertThat(expired.status()).isEqualTo(RunStatus.FAILED);
        assertThat(expired.failure()).isEqualTo(EXPIRED);
        assertThat(expired.pendingApprovalJson()).isNull();
        assertThat(expired.approvalJson()).isNull(); // an expiry is not a decision
        assertThat(expired.approvalMessage()).isEqualTo(POSTED);
        assertThat(expired.updatedAt()).isEqualTo(at);
        assertThat(store.find(run.runId()).orElseThrow()).isEqualTo(expired);
        assertThat(waitingBefore(T1.plusSeconds(60))).isEmpty();
        assertThat(unsentBefore(T1.plusSeconds(60))).isEmpty();
    }

    @Test
    void decidedRunsAndOtherCallsDoNotExpire() {
        var decided = waitingRun(PENDING);
        store.decideApproval(decided.runId(), PENDING, decision(PENDING, Decision.approved), T1);
        var other = waitingRun(PENDING);

        assertThat(store.expireApproval(decided.runId(), PENDING, EXPIRED, T1)).isEmpty();
        assertThat(store.expireApproval(other.runId(), PENDING.replace("42", "43"), EXPIRED, T1)).isEmpty();
        assertThat(store.expireApproval("run_missing", PENDING, EXPIRED, T1)).isEmpty();

        assertThat(store.find(decided.runId()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
        assertThat(store.find(other.runId()).orElseThrow().status()).isEqualTo(RunStatus.WAITING_APPROVAL);
    }

    @Test
    void anExpiredCallCannotBeDecided() {
        var run = waitingRun(PENDING);
        store.expireApproval(run.runId(), PENDING, EXPIRED, T1);

        assertThatThrownBy(() -> store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1))
                .isInstanceOf(IllegalTransitionException.class);
    }

    @Test
    void expiryAndDecisionHaveOneWinner() throws Exception {
        for (int i = 0; i < 5; i++) {
            var run = waitingRun(PENDING);
            var start = new CountDownLatch(1);
            var pool = Executors.newFixedThreadPool(2);
            try {
                var expiry = pool.submit(() -> {
                    start.await();
                    return store.expireApproval(run.runId(), PENDING, EXPIRED, T1).isPresent();
                });
                var decision = pool.submit(() -> {
                    start.await();
                    try {
                        store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1);
                        return true;
                    } catch (IllegalTransitionException e) {
                        return false;
                    }
                });
                start.countDown();
                assertThat(expiry.get() ^ decision.get()).isTrue();
                var status = store.find(run.runId()).orElseThrow().status();
                assertThat(status).isEqualTo(expiry.get() ? RunStatus.FAILED : RunStatus.QUEUED);
            } finally {
                pool.shutdownNow();
            }
        }
    }

    @Test
    void aNewPauseIsListedFromItsOwnStart() {
        var run = waitingRun(PENDING);
        store.decideApproval(run.runId(), PENDING, decision(PENDING, Decision.approved), T1);
        var later = T1.plusSeconds(30);
        store.update(run.runId(), to(RunStatus.RUNNING), later);
        store.update(run.runId(), new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null,
                PENDING.replace("7", "9"), null, null), later);

        assertThat(waitingBefore(later)).isEmpty();
        assertThat(waitingBefore(later.plusMillis(1))).containsExactly(run.runId());
    }

    // --- Tenant limits (ADR 0026, ADR 0027) ---

    private Run queuedRunOf(String tenant) {
        var run = Run.queued("run_" + UUID.randomUUID().toString().replace("-", ""), tenant, "triage", 3, INPUT, T0);
        store.create(run);
        return run;
    }

    private static RunUpdate cost(RunStatus status, String usd) {
        return new RunUpdate(status, null, new BigDecimal(usd), null, null, null, null);
    }


    @Test
    void startTakesSlotAndEndFreesIt() {
        var tenant = "slots-" + UUID.randomUUID();
        var first = queuedRunOf(tenant);
        var second = queuedRunOf(tenant);

        store.update(first.runId(), to(RunStatus.RUNNING), T1, 1);
        assertThat(store.running(tenant)).isEqualTo(1);
        assertThatThrownBy(() -> store.update(second.runId(), to(RunStatus.RUNNING), T1, 1))
                .isInstanceOf(TenantAtCapException.class);
        assertThat(store.find(second.runId()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);

        store.update(first.runId(), to(RunStatus.SUCCEEDED), T1, 1);
        assertThat(store.running(tenant)).isZero();
        assertThat(store.update(second.runId(), to(RunStatus.RUNNING), T1, 1).status()).isEqualTo(RunStatus.RUNNING);
        store.update(second.runId(), to(RunStatus.FAILED), T1, 1);
        assertThat(store.running(tenant)).isZero();
    }

    @Test
    void resumeTakesNoNewSlot() {
        var tenant = "resume-" + UUID.randomUUID();
        var run = queuedRunOf(tenant);
        store.update(run.runId(), to(RunStatus.RUNNING), T1, 1);

        store.update(run.runId(), to(RunStatus.RUNNING), T1.plusSeconds(1), 1);

        assertThat(store.running(tenant)).isEqualTo(1);
    }

    @Test
    void waitingForApprovalFreesSlotAndApprovedRunTakesOneAgain() {
        var tenant = "approval-" + UUID.randomUUID();
        var run = queuedRunOf(tenant);
        store.update(run.runId(), to(RunStatus.RUNNING), T1, 1);

        store.update(run.runId(), new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null, PENDING, null, null), T1, 1);
        assertThat(store.running(tenant)).isZero();

        store.decideApproval(run.runId(), PENDING, ApprovalDecision.of(PENDING, Decision.approved, "api:t", T1), T1);
        store.update(run.runId(), to(RunStatus.RUNNING), T1, 1);
        assertThat(store.running(tenant)).isEqualTo(1);
    }

    @Test
    void slotsArePerTenant() {
        var busy = "busy-" + UUID.randomUUID();
        var quiet = "quiet-" + UUID.randomUUID();
        store.update(queuedRunOf(busy).runId(), to(RunStatus.RUNNING), T1, 1);

        store.update(queuedRunOf(quiet).runId(), to(RunStatus.RUNNING), T1, 1);

        assertThat(store.running(busy)).isEqualTo(1);
        assertThat(store.running(quiet)).isEqualTo(1);
    }

    @Test
    void concurrentStartsNeverExceedTheCap() throws Exception {
        var tenant = "race-" + UUID.randomUUID();
        var ids = new ArrayList<String>();
        for (int i = 0; i < 8; i++) {
            ids.add(queuedRunOf(tenant).runId());
        }
        var ready = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(8);
        try {
            var tasks = new ArrayList<Callable<Boolean>>();
            for (var id : ids) {
                tasks.add(() -> {
                    ready.await();
                    try {
                        store.update(id, to(RunStatus.RUNNING), T1, 3);
                        return true;
                    } catch (TenantAtCapException e) {
                        return false;
                    }
                });
            }
            var futures = tasks.stream().map(pool::submit).toList();
            ready.countDown();
            int started = 0;
            for (var future : futures) {
                started += future.get() ? 1 : 0;
            }
            assertThat(started).isEqualTo(3);
            assertThat(store.running(tenant)).isEqualTo(3);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void setAsideFreesSlotAndQueuesUntilItIsDue() {
        var tenant = "aside-" + UUID.randomUUID();
        var run = queuedRunOf(tenant);
        store.update(run.runId(), to(RunStatus.RUNNING), T1, 1);

        var queued = store.setAside(run.runId(), 30, T1.plusSeconds(30), T1);

        assertThat(queued.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(queued.setAsideSeconds()).isEqualTo(30);
        assertThat(store.find(run.runId()).orElseThrow()).isEqualTo(queued);
        assertThat(store.running(tenant)).isZero();
        assertThat(queuedBefore(T1.plusSeconds(29))).doesNotContain(run.runId());
        assertThat(queuedBefore(T1.plusSeconds(31))).contains(run.runId());

        store.update(run.runId(), to(RunStatus.RUNNING), T1.plusSeconds(31), 1);
        assertThat(store.setAside(run.runId(), 60, T1.plusSeconds(91), T1.plusSeconds(31)).setAsideSeconds())
                .isEqualTo(90);
    }

    @Test
    void onlyRunningRunIsSetAside() {
        var run = queuedRun();

        assertThatThrownBy(() -> store.setAside(run.runId(), 30, T1, T1)).isInstanceOf(IllegalTransitionException.class);
        assertThatThrownBy(() -> store.setAside("run_missing", 30, T1, T1)).isInstanceOf(RunNotFoundException.class);
    }

    @Test
    void costIncreasesAddUpToTheTenantsDailySpend() {
        var tenant = "spend-" + UUID.randomUUID();
        var a = queuedRunOf(tenant);
        var b = queuedRunOf(tenant);
        var day = LocalDate.of(2026, 11, 16);
        store.update(a.runId(), cost(RunStatus.RUNNING, "0.10"), T1);
        store.update(a.runId(), cost(RunStatus.RUNNING, "0.25"), T1);
        store.update(b.runId(), cost(RunStatus.RUNNING, "0.05"), T1);
        store.update(b.runId(), cost(RunStatus.SUCCEEDED, "0.04"), T1); // a lower total is not counted back

        assertThat(store.spend(tenant, day)).isEqualByComparingTo("0.30");
        assertThat(store.spend(tenant, day.plusDays(1))).isEqualByComparingTo("0");
        assertThat(store.spend("other-" + UUID.randomUUID(), day)).isEqualByComparingTo("0");
    }

    @Test
    void spendCountsOnTheUtcDayOfTheUpdate() {
        var tenant = "midnight-" + UUID.randomUUID();
        var run = queuedRunOf(tenant);
        store.update(run.runId(), cost(RunStatus.RUNNING, "0.10"), Instant.parse("2026-11-16T23:59:59Z"));
        store.update(run.runId(), cost(RunStatus.RUNNING, "0.30"), Instant.parse("2026-11-17T00:00:01Z"));

        assertThat(store.spend(tenant, LocalDate.of(2026, 11, 16))).isEqualByComparingTo("0.10");
        assertThat(store.spend(tenant, LocalDate.of(2026, 11, 17))).isEqualByComparingTo("0.20");
    }

    @Test
    void queuedRunFailsOnlyWhileQueued() {
        var run = queuedRun();
        var failure = new Run.Failure("tenant_budget", "over the cap");

        var failed = store.failQueued(run.runId(), failure, T1).orElseThrow();

        assertThat(failed.status()).isEqualTo(RunStatus.FAILED);
        assertThat(failed.failure()).isEqualTo(failure);
        assertThat(store.find(run.runId()).orElseThrow()).isEqualTo(failed);
        assertThat(queuedBefore(T1.plusSeconds(1))).doesNotContain(run.runId());
        assertThat(store.failQueued(run.runId(), failure, T1)).isEmpty();
        assertThat(store.failQueued("run_missing", failure, T1)).isEmpty();
    }

    @Test
    void runningRunsAreListedByLastUpdate() {
        var run = queuedRun();
        store.update(run.runId(), to(RunStatus.RUNNING), T1);
        store.update(run.runId(), to(RunStatus.RUNNING), T1.plusSeconds(60));

        assertThat(store.findRunningBefore(T1.plusSeconds(60), 100)).extracting(Run::runId).doesNotContain(run.runId());
        assertThat(store.findRunningBefore(T1.plusSeconds(61), 100)).extracting(Run::runId).contains(run.runId());

        store.update(run.runId(), to(RunStatus.SUCCEEDED), T1.plusSeconds(90));
        assertThat(store.findRunningBefore(T1.plusSeconds(3600), 100)).extracting(Run::runId).doesNotContain(run.runId());
    }

    @Test
    void stalledRunFailsAndFreesItsSlotOnlyIfNotUpdatedSince() {
        var tenant = "stalled-" + UUID.randomUUID();
        var run = queuedRunOf(tenant);
        var running = store.update(run.runId(), to(RunStatus.RUNNING), T1, 1);
        var failure = new Run.Failure("error", "no progress");

        assertThat(store.failStalled(run.runId(), T0, failure, T1.plusSeconds(10))).isEmpty();
        var failed = store.failStalled(run.runId(), running.updatedAt(), failure, T1.plusSeconds(10)).orElseThrow();

        assertThat(failed.status()).isEqualTo(RunStatus.FAILED);
        assertThat(failed.failure()).isEqualTo(failure);
        assertThat(store.running(tenant)).isZero();
        assertThat(store.failStalled(run.runId(), failed.updatedAt(), failure, T1.plusSeconds(20))).isEmpty();
        assertThat(store.failStalled("run_missing", T1, failure, T1)).isEmpty();
    }
}
