package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.store.RunStore;
import java.time.Clock;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Approval sweep: posts pending calls of runs that have waited longer than {@link #UNSENT_AFTER}
 * without being posted (the post in the worker's report failed). Stops at the first failure: the
 * channel is down or rate limited, so the rest waits for the next sweep. Called by the sweep
 * schedule locally; on Lambda by EventBridge Scheduler (M6).
 */
@Component
public class ApprovalSweeper {

    private static final Logger log = LoggerFactory.getLogger(ApprovalSweeper.class);
    /** Leaves the worker's own post time to finish before the sweep tries. */
    static final Duration UNSENT_AFTER = Duration.ofSeconds(60);
    /** Slack allows about one message per second per channel; the next sweep takes the rest. */
    static final int PAGE = 20;

    private final RunStore runs;
    private final ApprovalNotifier notifier;
    private final Clock clock;

    public ApprovalSweeper(RunStore runs, ApprovalNotifier notifier, Clock clock) {
        this.runs = runs;
        this.notifier = notifier;
        this.clock = clock;
    }

    /** One sweep. Returns how many pending calls were posted. */
    public int postUnsentOnce() {
        int posted = 0;
        for (var run : runs.findUnsentApprovalsBefore(clock.instant().minus(UNSENT_AFTER), PAGE)) {
            var outcome = notifier.post(run);
            if (outcome == ApprovalNotifier.Outcome.FAILED) {
                break;
            }
            if (outcome == ApprovalNotifier.Outcome.POSTED) {
                posted++;
            }
        }
        if (posted > 0) {
            log.info("Approval sweep: posted {} pending call(s)", posted);
        }
        return posted;
    }
}
