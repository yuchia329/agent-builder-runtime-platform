package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.queue.RunMessage;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import dev.agentplatform.controlplane.trace.TestTracing;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

class RunSweeperTest {

    private static final Instant T0 = Instant.parse("2026-11-17T10:00:00Z");
    private static final Duration AFTER = Duration.ofMinutes(5);

    private final InMemoryRunStore runs = new InMemoryRunStore();
    private final List<String> sent = new ArrayList<>();
    private final List<RunMessage> messages = new ArrayList<>();
    private final Set<String> failing = new HashSet<>();
    private final RunQueuePublisher queue = (message, delaySeconds) -> {
        if (failing.contains(message.runId())) {
            throw new RunQueueException("queue down", null);
        }
        sent.add(message.runId());
        messages.add(message);
    };

    private RunSweeper sweeperAt(Instant now) {
        return new RunSweeper(runs, queue, Clock.fixed(now, ZoneOffset.UTC), AFTER);
    }

    private Run queued(String id, Instant at) {
        var run = Run.queued(id, "t1", "triage", 1, "{}", at);
        runs.create(run);
        return run;
    }

    @Test
    void reSentRunContinuesItsOwnTrace() {
        var traceParent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
        runs.create(Run.queued("run_traced", "t1", "triage", 1, "{}", "api:t1", traceParent, T0));
        var test = new TestTracing();

        new RunSweeper(runs, queue, Clock.fixed(T0.plus(AFTER).plusSeconds(1), ZoneOffset.UTC), AFTER, test.tracing)
                .sweepOnce();

        var requeue = test.span("run.requeue");
        assertThat(requeue.getTraceId()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");
        assertThat(requeue.getParentSpanId()).isEqualTo("00f067aa0ba902b7");
        assertThat(messages.getFirst().traceParent())
                .isEqualTo("00-4bf92f3577b34da6a3ce929d0e0e4736-" + requeue.getSpanId() + "-01");
    }

    @Test
    void runQueuedTooLongIsSentAgainWithItsTenant() {
        queued("run_old", T0);
        queued("run_new", T0.plus(AFTER));

        var count = sweeperAt(T0.plus(AFTER).plusSeconds(1)).sweepOnce();

        assertThat(count).isEqualTo(1);
        assertThat(sent).containsExactly("run_old");
    }

    @Test
    void aSentRunWaitsAnotherIntervalBeforeTheNextResend() {
        queued("run_old", T0);
        var now = T0.plus(AFTER).plusSeconds(1);

        sweeperAt(now).sweepOnce();
        sweeperAt(now.plusSeconds(60)).sweepOnce();
        sweeperAt(now.plus(AFTER).plusSeconds(1)).sweepOnce();

        assertThat(sent).containsExactly("run_old", "run_old");
    }

    @Test
    void runThatStartedIsNotSent() {
        queued("run_started", T0);
        runs.update("run_started", new RunUpdate(RunStatus.RUNNING, 0, null, null, null, null, null), T0);

        assertThat(sweeperAt(T0.plus(AFTER).plusSeconds(1)).sweepOnce()).isZero();
        assertThat(sent).isEmpty();
    }

    @Test
    void aFailedResendDoesNotStopTheSweepAndIsRetriedLater() {
        queued("run_a", T0);
        queued("run_b", T0.plusSeconds(1));
        failing.add("run_a");
        var now = T0.plus(AFTER).plusSeconds(2);

        assertThat(sweeperAt(now).sweepOnce()).isEqualTo(1);
        assertThat(sent).containsExactly("run_b");

        failing.clear();
        sweeperAt(now.plus(AFTER).plusSeconds(1)).sweepOnce();
        assertThat(sent).contains("run_a");
    }

    @Test
    void manyStuckRunsAreTakenPageByPage() {
        for (int i = 0; i < RunSweeper.PAGE + 5; i++) {
            queued("run_%03d".formatted(i), T0.plusMillis(i));
        }

        assertThat(sweeperAt(T0.plus(AFTER).plusSeconds(10)).sweepOnce()).isEqualTo(RunSweeper.PAGE + 5);
    }
}
