package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.store.RunStore;
import java.time.Clock;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Approval sweep, two jobs. {@link #postUnsentOnce}: posts pending calls of runs that have waited
 * longer than {@link #UNSENT_AFTER} without being posted (the post in the worker's report failed),
 * stopping at the first failure: the channel is down or rate limited, so the rest waits for the
 * next sweep. {@link #expireOnce}: fails runs left WAITING_APPROVAL longer than the approval
 * timeout with {@code approval_expired} and shows that on their message. Called by the sweep
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
    private final ApprovalChannel channel;
    private final Clock clock;
    private final Duration timeout;

    @Autowired
    public ApprovalSweeper(RunStore runs, ApprovalNotifier notifier, ApprovalChannel channel, Clock clock,
                           PlatformProperties props) {
        this(runs, notifier, channel, clock, props.approvalTimeout());
    }

    ApprovalSweeper(RunStore runs, ApprovalNotifier notifier, ApprovalChannel channel, Clock clock,
                    Duration timeout) {
        this.runs = runs;
        this.notifier = notifier;
        this.channel = channel;
        this.clock = clock;
        this.timeout = timeout;
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

    /**
     * One expiry sweep: fails up to {@link #PAGE} runs waiting longer than the timeout (each only
     * if still waiting on the same call, so a decision made meanwhile wins) and shows the expiry
     * on each posted message. A failed message update keeps the expiry; after one, the rest of this
     * sweep skips Slack (a later click on such a message still shows "Expired"). Returns how many
     * runs expired.
     */
    public int expireOnce() {
        var now = clock.instant();
        int expired = 0;
        boolean showing = true;
        for (var run : runs.findWaitingApprovalsBefore(now.minus(timeout), PAGE)) {
            var pending = run.pendingApprovalJson();
            if (pending == null) {
                continue;
            }
            var request = ApprovalRequest.of(run); // before the pending call is cleared
            var failure = ApprovalDecision.expiredFailure(pending, timeout);
            var done = runs.expireApproval(run.runId(), pending, failure, now);
            if (done.isEmpty()) {
                continue; // decided or moved on since the index was read
            }
            expired++;
            log.info("Run {} failed: {}", run.runId(), failure.message());
            var message = done.get().approvalMessage();
            if (showing && message != null) {
                try {
                    channel.showOutcome(message, request, ApprovalOutcome.expired(failure.message()));
                } catch (ApprovalChannelException e) {
                    log.warn("Expiry of run {} not shown in Slack: {}", run.runId(), e.getMessage());
                    showing = false;
                }
            }
        }
        if (expired > 0) {
            log.info("Approval sweep: expired {} run(s)", expired);
        }
        return expired;
    }
}
