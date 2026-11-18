package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.store.RunStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Posts a waiting run's pending call to the {@link ApprovalChannel} and records where
 * ({@link RunStore#markApprovalSent}). Never throws: a failed post leaves the run unsent for
 * {@link ApprovalSweeper}, and the worker's status report succeeds either way.
 *
 * <p>The worker's report and the sweep can race and post the same call twice; only the first post
 * is recorded. A duplicate message is harmless: a decision is recorded once, whichever is clicked.
 */
@Component
public class ApprovalNotifier {

    private static final Logger log = LoggerFactory.getLogger(ApprovalNotifier.class);

    public enum Outcome { POSTED, SKIPPED, FAILED }

    private final ApprovalChannel channel;
    private final RunStore runs;

    public ApprovalNotifier(ApprovalChannel channel, RunStore runs) {
        this.channel = channel;
        this.runs = runs;
    }

    /**
     * Posts {@code run}'s pending call if it waits for approval and was not posted yet.
     *
     * @return POSTED (posted and recorded), SKIPPED (nothing to post, or the run changed meanwhile),
     *     FAILED (not posted; the sweep tries again)
     */
    public Outcome post(Run run) {
        if (run.status() != RunStatus.WAITING_APPROVAL || run.pendingApprovalJson() == null
                || run.approvalMessage() != null) {
            return Outcome.SKIPPED;
        }
        ApprovalMessage message;
        try {
            message = channel.post(ApprovalRequest.of(run));
        } catch (RuntimeException e) {
            log.warn("Approval request for run {} not posted; the sweep tries again: {}", run.runId(), e.getMessage());
            return Outcome.FAILED;
        }
        try {
            if (runs.markApprovalSent(run.runId(), run.pendingApprovalJson(), message)) {
                return Outcome.POSTED;
            }
            log.info("Run {} was decided, paused again or posted meanwhile; message {} in {} not recorded",
                    run.runId(), message.messageId(), message.channel());
            return Outcome.SKIPPED;
        } catch (RuntimeException e) {
            // Posted but not recorded: the sweep posts it again, which is the harmless duplicate.
            log.warn("Approval request for run {} posted but not recorded: {}", run.runId(), e.getMessage());
            return Outcome.FAILED;
        }
    }
}
