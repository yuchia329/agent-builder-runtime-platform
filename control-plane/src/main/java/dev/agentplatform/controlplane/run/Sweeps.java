package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.approval.ApprovalSweeper;
import org.springframework.stereotype.Component;

/**
 * The three sweeps in their fixed order: re-enqueue stuck runs, post unsent approval requests,
 * expire approvals nobody decided. Called by {@link RunSweepSchedule} while the app runs as a
 * server, and by {@code lambda.LambdaHandler} when EventBridge Scheduler invokes the function.
 */
@Component
public class Sweeps {

    private final RunSweeper sweeper;
    private final ApprovalSweeper approvals;

    public Sweeps(RunSweeper sweeper, ApprovalSweeper approvals) {
        this.sweeper = sweeper;
        this.approvals = approvals;
    }

    /** What one pass did. */
    public record Result(int requeued, int posted, int expired) {}

    public Result runOnce() {
        var requeued = sweeper.sweepOnce();
        var posted = approvals.postUnsentOnce();
        var expired = approvals.expireOnce();
        return new Result(requeued, posted, expired);
    }
}
