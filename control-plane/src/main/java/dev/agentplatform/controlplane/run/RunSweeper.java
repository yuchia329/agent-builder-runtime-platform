package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.queue.RunMessage;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.RunStore;
import java.time.Clock;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Re-enqueue sweep: sends the queue message again for runs left QUEUED longer than
 * {@code queuedAfter} (saved, but the enqueue failed or the message was lost). A run that was only
 * slow to start gets a second message, which workers ignore (lease, status check).
 *
 * <p>Each run is first touched (its queued-at time set to now, only if still QUEUED), then
 * published; so a run that just left QUEUED is skipped, and a run is re-sent at most once per
 * {@code queuedAfter}. Called by {@link RunSweepSchedule} locally; on Lambda by EventBridge
 * Scheduler (M6).
 */
@Component
public class RunSweeper {

    private static final Logger log = LoggerFactory.getLogger(RunSweeper.class);
    static final int PAGE = 100;
    static final int MAX_PAGES = 10; // at most 1,000 runs per sweep; the next sweep takes the rest

    private final RunStore runs;
    private final RunQueuePublisher queue;
    private final Clock clock;
    private final Duration queuedAfter;

    @Autowired
    public RunSweeper(RunStore runs, RunQueuePublisher queue, Clock clock, PlatformProperties props) {
        this(runs, queue, clock, Duration.ofSeconds(props.sweepQueuedAfterSeconds()));
    }

    RunSweeper(RunStore runs, RunQueuePublisher queue, Clock clock, Duration queuedAfter) {
        this.runs = runs;
        this.queue = queue;
        this.clock = clock;
        this.queuedAfter = queuedAfter;
    }

    /** One sweep. Returns how many runs were re-enqueued. */
    public int sweepOnce() {
        var now = clock.instant();
        var cutoff = now.minus(queuedAfter);
        int sent = 0;
        for (int page = 0; page < MAX_PAGES; page++) {
            var stuck = runs.findQueuedBefore(cutoff, PAGE);
            if (stuck.isEmpty()) {
                break;
            }
            for (var run : stuck) {
                if (!runs.touchQueued(run.runId(), now)) {
                    continue; // left QUEUED since the index was read
                }
                try {
                    queue.publish(new RunMessage(run.runId(), run.tenantId()));
                    sent++;
                } catch (RunQueueException e) {
                    log.warn("Re-enqueue of run {} failed; retried next sweep: {}", run.runId(), e.getMessage());
                }
            }
            if (stuck.size() < PAGE) {
                break;
            }
        }
        if (sent > 0) {
            log.info("Re-enqueue sweep: sent {} run(s) left QUEUED over {}", sent, queuedAfter);
        }
        return sent;
    }
}
