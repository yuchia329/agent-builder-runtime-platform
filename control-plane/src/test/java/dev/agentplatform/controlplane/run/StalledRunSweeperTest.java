package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.store.InMemoryRunStore;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

class StalledRunSweeperTest {

    private static final Instant T0 = Instant.parse("2026-11-21T10:00:00Z");
    private static final Duration AFTER = Duration.ofMinutes(90);

    private final InMemoryRunStore runs = new InMemoryRunStore();

    private String runningRun(Instant lastUpdate) {
        var run = Run.queued(RunService.newRunId(), "t1", "triage", 1, "{}", T0);
        runs.create(run);
        runs.update(run.runId(), new RunUpdate(RunStatus.RUNNING, null, null, null, null, null, null), lastUpdate, 5);
        return run.runId();
    }

    private int sweepAt(Instant now) {
        return new StalledRunSweeper(runs, Clock.fixed(now, ZoneOffset.UTC), AFTER).sweepOnce();
    }

    @Test
    void failsRunsWithNoUpdateForTooLongAndFreesTheirSlots() {
        var stalled = runningRun(T0);
        var busy = runningRun(T0.plus(AFTER));

        assertThat(sweepAt(T0.plus(AFTER).plusSeconds(1))).isEqualTo(1);

        var failed = runs.find(stalled).orElseThrow();
        assertThat(failed.status()).isEqualTo(RunStatus.FAILED);
        assertThat(failed.failure().reason()).isEqualTo("error");
        assertThat(failed.failure().message()).contains("90 minutes");
        assertThat(runs.find(busy).orElseThrow().status()).isEqualTo(RunStatus.RUNNING);
        assertThat(runs.running("t1")).isEqualTo(1);
    }

    @Test
    void nothingStalledNothingChanged() {
        runningRun(T0);

        assertThat(sweepAt(T0.plus(AFTER))).isZero();
    }
}
