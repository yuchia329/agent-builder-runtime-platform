package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
        var run = Run.queued("run_" + UUID.randomUUID().toString().replace("-", ""), "t1", "triage", 3, INPUT, T0);
        store.create(run);
        return run;
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
}
