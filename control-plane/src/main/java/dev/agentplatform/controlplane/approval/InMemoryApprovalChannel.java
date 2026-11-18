package dev.agentplatform.controlplane.approval;

import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@link ApprovalChannel} without Slack: keeps requests in memory and logs them, so a person
 * decides with {@code POST /runs/{id}/approval}. Used in tests and when no Slack bot is
 * configured. Thread-safe.
 */
public class InMemoryApprovalChannel implements ApprovalChannel {

    private static final Logger log = LoggerFactory.getLogger(InMemoryApprovalChannel.class);
    public static final String CHANNEL = "memory";

    private final List<ApprovalRequest> posted = new ArrayList<>();
    private int failures;

    @Override
    public synchronized ApprovalMessage post(ApprovalRequest request) {
        if (failures > 0) {
            failures--;
            throw new ApprovalChannelException("Approval channel unavailable (test failure)");
        }
        posted.add(request);
        log.info("Run {} waits for approval of {} at step {}; decide with POST /runs/{}/approval",
                request.runId(), request.tool(), request.step(), request.runId());
        return new ApprovalMessage(CHANNEL, Integer.toString(posted.size()));
    }

    /** Requests posted so far, oldest first (a copy). */
    public synchronized List<ApprovalRequest> posted() {
        return List.copyOf(posted);
    }

    /** Makes the next {@code times} posts fail. */
    public synchronized void failNext(int times) {
        failures = times;
    }
}
