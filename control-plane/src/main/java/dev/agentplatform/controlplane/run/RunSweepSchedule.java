package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.approval.ApprovalSweeper;
import java.util.concurrent.TimeUnit;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Runs the sweeps (re-enqueue, approval posts, approval expiry) every {@code SWEEP_INTERVAL_SECONDS} while the app runs as a server. Off when the
 * interval is 0 (tests, Lambda: a function between requests is frozen, so EventBridge Scheduler
 * calls the sweep there instead, M6).
 */
@Component
@ConditionalOnExpression("${platform.sweep.interval-seconds:60} > 0")
class RunSweepSchedule {

    private final RunSweeper sweeper;
    private final ApprovalSweeper approvals;

    RunSweepSchedule(RunSweeper sweeper, ApprovalSweeper approvals) {
        this.sweeper = sweeper;
        this.approvals = approvals;
    }

    @Scheduled(
            initialDelayString = "${platform.sweep.interval-seconds:60}",
            fixedDelayString = "${platform.sweep.interval-seconds:60}",
            timeUnit = TimeUnit.SECONDS)
    void sweep() {
        sweeper.sweepOnce();
        approvals.postUnsentOnce();
        approvals.expireOnce();
    }
}
