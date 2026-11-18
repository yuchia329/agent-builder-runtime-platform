package dev.agentplatform.controlplane.approval;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

/** ApprovalNotifier and ApprovalSweeper with in-memory store and channel. */
class ApprovalSweeperTest {

    private static final Instant T0 = Instant.parse("2026-11-17T09:00:00Z");
    private static final String PENDING =
            "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}";

    private final InMemoryRunStore runs = new InMemoryRunStore();
    private final InMemoryApprovalChannel channel = new InMemoryApprovalChannel();
    private final ApprovalNotifier notifier = new ApprovalNotifier(channel, runs);

    private Run waiting(String id, Instant at) {
        runs.create(Run.queued(id, "t1", "triage", 1, "{}", at));
        runs.update(id, new RunUpdate(RunStatus.RUNNING, null, null, null, null, null, null), at);
        return runs.update(id, new RunUpdate(RunStatus.WAITING_APPROVAL, 6, null, null, PENDING, null, null), at);
    }

    private ApprovalSweeper sweeperAt(Instant now) {
        return new ApprovalSweeper(runs, notifier, Clock.fixed(now, ZoneOffset.UTC));
    }

    @Test
    void notifierPostsThePendingCallAndRecordsWhere() {
        var run = waiting("run_a", T0);

        assertThat(notifier.post(run)).isEqualTo(ApprovalNotifier.Outcome.POSTED);

        assertThat(channel.posted()).containsExactly(ApprovalRequest.of(run));
        var request = channel.posted().getFirst();
        assertThat(request.step()).isEqualTo(7);
        assertThat(request.tool()).isEqualTo("github.close_issue");
        assertThat(request.arguments().get("issue_number").asInt()).isEqualTo(42);
        assertThat(runs.find("run_a").orElseThrow().approvalMessage())
                .isEqualTo(new ApprovalMessage(InMemoryApprovalChannel.CHANNEL, "1"));
    }

    @Test
    void notifierSkipsRunsNotWaitingOrAlreadyPosted() {
        var run = waiting("run_a", T0);
        notifier.post(run);

        assertThat(notifier.post(runs.find("run_a").orElseThrow())).isEqualTo(ApprovalNotifier.Outcome.SKIPPED);
        assertThat(notifier.post(Run.queued("run_b", "t1", "triage", 1, "{}", T0)))
                .isEqualTo(ApprovalNotifier.Outcome.SKIPPED);
        assertThat(channel.posted()).hasSize(1);
    }

    @Test
    void failedPostIsReportedNotThrown() {
        var run = waiting("run_a", T0);
        channel.failNext(1);

        assertThat(notifier.post(run)).isEqualTo(ApprovalNotifier.Outcome.FAILED);
        assertThat(runs.find("run_a").orElseThrow().approvalMessage()).isNull();
    }

    @Test
    void runDecidedWhilePostingIsNotRecorded() {
        var run = waiting("run_a", T0);
        runs.decideApproval("run_a", PENDING,
                dev.agentplatform.controlplane.run.ApprovalDecision.of(PENDING,
                        dev.agentplatform.controlplane.run.ApprovalDecision.Decision.approved, "api:t1", T0), T0);

        assertThat(notifier.post(run)).isEqualTo(ApprovalNotifier.Outcome.SKIPPED); // stale copy of the run
        assertThat(runs.find("run_a").orElseThrow().approvalMessage()).isNull();
    }

    @Test
    void sweepPostsRunsUnsentForAMinute() {
        waiting("run_old", T0);
        waiting("run_new", T0.plusSeconds(30));

        assertThat(sweeperAt(T0.plusSeconds(61)).postUnsentOnce()).isEqualTo(1);

        assertThat(channel.posted()).extracting(ApprovalRequest::runId).containsExactly("run_old");
        assertThat(sweeperAt(T0.plusSeconds(61)).postUnsentOnce()).isZero(); // posted once
        assertThat(sweeperAt(T0.plusSeconds(91)).postUnsentOnce()).isEqualTo(1);
    }

    @Test
    void sweepStopsAtTheFirstFailure() {
        waiting("run_a", T0);
        waiting("run_b", T0.plusMillis(1));
        channel.failNext(1);

        assertThat(sweeperAt(T0.plusSeconds(120)).postUnsentOnce()).isZero();
        assertThat(channel.posted()).isEmpty(); // run_b waits for the next sweep

        assertThat(sweeperAt(T0.plusSeconds(180)).postUnsentOnce()).isEqualTo(2);
    }
}
