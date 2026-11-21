package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.approval.ApprovalSweeper;
import dev.agentplatform.controlplane.trace.RunTracing;
import org.springframework.stereotype.Component;

/**
 * The four sweeps in their fixed order: re-enqueue stuck runs, post unsent approval requests,
 * expire approvals nobody decided, fail stalled RUNNING runs. Called by {@link RunSweepSchedule} while the app runs as a
 * server, and by {@code lambda.LambdaHandler} when EventBridge Scheduler invokes the function.
 * Each pass is one "sweeps" span in its own trace; a re-enqueued run gets a span in its own trace.
 */
@Component
public class Sweeps {

    private final RunSweeper sweeper;
    private final ApprovalSweeper approvals;
    private final StalledRunSweeper stalled;
    private final RunTracing tracing;

    public Sweeps(RunSweeper sweeper, ApprovalSweeper approvals, StalledRunSweeper stalled, RunTracing tracing) {
        this.sweeper = sweeper;
        this.approvals = approvals;
        this.stalled = stalled;
        this.tracing = tracing;
    }

    /** What one pass did. */
    public record Result(int requeued, int posted, int expired, int stalled) {}

    public Result runOnce() {
        var span = tracing.startRoot("sweeps");
        return RunTracing.inSpan(span, () -> {
            var result = new Result(sweeper.sweepOnce(), approvals.postUnsentOnce(), approvals.expireOnce(),
                    stalled.sweepOnce());
            span.setAttribute("sweep.requeued", (long) result.requeued());
            span.setAttribute("sweep.posted", (long) result.posted());
            span.setAttribute("sweep.expired", (long) result.expired());
            span.setAttribute("sweep.stalled", (long) result.stalled());
            return result;
        });
    }
}
