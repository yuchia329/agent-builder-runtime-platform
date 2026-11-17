package dev.agentplatform.controlplane.run;

import java.util.concurrent.TimeUnit;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Runs the sweep every {@code SWEEP_INTERVAL_SECONDS} while the app runs as a server. Off when the
 * interval is 0 (tests, Lambda: a function between requests is frozen, so EventBridge Scheduler
 * calls the sweep there instead, M6).
 */
@Component
@ConditionalOnExpression("${platform.sweep.interval-seconds:60} > 0")
class RunSweepSchedule {

    private final RunSweeper sweeper;

    RunSweepSchedule(RunSweeper sweeper) {
        this.sweeper = sweeper;
    }

    @Scheduled(
            initialDelayString = "${platform.sweep.interval-seconds:60}",
            fixedDelayString = "${platform.sweep.interval-seconds:60}",
            timeUnit = TimeUnit.SECONDS)
    void sweep() {
        sweeper.sweepOnce();
    }
}
