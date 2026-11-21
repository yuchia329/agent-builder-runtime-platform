package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.store.RunStore;
import java.time.Clock;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Stalled-run sweep: fails runs RUNNING with no update for {@code stalledAfter} (default 90
 * minutes, longer than any run's working time), so a run whose worker is gone (its message went to
 * the dead-letter queue) does not hold a slot of its tenant forever (ADR 0026). A run updated since
 * it was listed is left alone. Its worker, if one is somehow still there, gets 409 on its next
 * report and stops.
 */
@Component
public class StalledRunSweeper {

    private static final Logger log = LoggerFactory.getLogger(StalledRunSweeper.class);
    static final int LIMIT = 100; // the next sweep takes the rest

    private final RunStore runs;
    private final Clock clock;
    private final Duration stalledAfter;

    @Autowired
    public StalledRunSweeper(RunStore runs, Clock clock, PlatformProperties props) {
        this(runs, clock, Duration.ofSeconds(props.sweepRunningStalledAfterSeconds()));
    }

    StalledRunSweeper(RunStore runs, Clock clock, Duration stalledAfter) {
        this.runs = runs;
        this.clock = clock;
        this.stalledAfter = stalledAfter;
    }

    /** One sweep. Returns how many runs were failed. */
    public int sweepOnce() {
        var now = clock.instant();
        var minutes = stalledAfter.toMinutes();
        var failure = new Run.Failure("error",
                "No progress for %d minutes: the run's worker is gone".formatted(minutes));
        int failed = 0;
        for (var run : runs.findRunningBefore(now.minus(stalledAfter), LIMIT)) {
            if (runs.failStalled(run.runId(), run.updatedAt(), failure, now).isPresent()) {
                failed++;
                log.warn("Run {} of tenant {} failed: RUNNING with no update since {}", run.runId(), run.tenantId(),
                        run.updatedAt());
            }
        }
        return failed;
    }
}
