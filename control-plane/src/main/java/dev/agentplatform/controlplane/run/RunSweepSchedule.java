package dev.agentplatform.controlplane.run;

import java.util.concurrent.TimeUnit;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Runs the {@link Sweeps} every {@code SWEEP_INTERVAL_SECONDS} while the app runs as a server.
 * Off when the interval is 0: in tests, and on Lambda, where a function is frozen between
 * requests and EventBridge Scheduler invokes the sweeps instead ({@code lambda.LambdaHandler}).
 */
@Component
@ConditionalOnExpression("${platform.sweep.interval-seconds:60} > 0")
class RunSweepSchedule {

    private final Sweeps sweeps;

    RunSweepSchedule(Sweeps sweeps) {
        this.sweeps = sweeps;
    }

    @Scheduled(
            initialDelayString = "${platform.sweep.interval-seconds:60}",
            fixedDelayString = "${platform.sweep.interval-seconds:60}",
            timeUnit = TimeUnit.SECONDS)
    void sweep() {
        sweeps.runOnce();
    }
}
