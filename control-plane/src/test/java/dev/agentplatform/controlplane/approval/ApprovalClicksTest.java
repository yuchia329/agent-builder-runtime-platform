package dev.agentplatform.controlplane.approval;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import java.time.Clock;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ApprovalClicksTest {

    private static final String PENDING =
            "{\"step\":5,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}";
    private static final ApprovalMessage MESSAGE = new ApprovalMessage("C0123456789", "1794980400.000100");

    private final InMemoryRunStore runs = new InMemoryRunStore();
    private final InMemoryApprovalChannel channel = new InMemoryApprovalChannel();
    private final InMemoryRunQueuePublisher queue = new InMemoryRunQueuePublisher();
    private RunService service;
    private ApprovalClicks clicks;
    private String runId;

    @BeforeEach
    void setUp() {
        var agents = new InMemoryAgentStore(Clock.systemUTC());
        agents.createAgent("t1", "triage");
        agents.publishVersion("t1", "triage", "github.issue.opened", "{}");
        service = new RunService(agents, runs, queue, new ApprovalNotifier(channel, runs), Clock.systemUTC());
        clicks = new ApprovalClicks(service, runs, channel);
        runId = service.start("t1", "triage", "{}").runId();
        service.update(runId, new RunUpdate(RunStatus.RUNNING, null, null, null, null, null, null));
        service.update(runId, new RunUpdate(RunStatus.WAITING_APPROVAL, 4, null, null, PENDING, null, null));
    }

    private SlackInteractions.Click click(Decision decision, int step, String user) {
        return new SlackInteractions.Click(runId, step, decision, user, MESSAGE);
    }

    @Test
    void approveDecidesAsTheSlackUserAndShowsIt() {
        var enqueued = queue.bodies().size();

        clicks.apply(click(Decision.approved, 5, "U0APPROVER1"));

        var run = runs.find(runId).orElseThrow();
        assertThat(run.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(run.approvalJson()).contains("\"decided_by\":\"slack:U0APPROVER1\"", "\"decision\":\"approved\"");
        assertThat(queue.bodies()).hasSize(enqueued + 1);
        var shown = channel.shown().getLast();
        assertThat(shown.message()).isEqualTo(MESSAGE);
        assertThat(shown.request().tool()).isEqualTo("github.close_issue");
        assertThat(shown.request().arguments().get("issue_number").asInt()).isEqualTo(42);
        assertThat(shown.outcome().decision()).isEqualTo(Decision.approved);
        assertThat(shown.outcome().decidedBy()).isEqualTo("slack:U0APPROVER1");
    }

    @Test
    void rejectFailsTheRun() {
        clicks.apply(click(Decision.rejected, 5, "U0APPROVER1"));

        var run = runs.find(runId).orElseThrow();
        assertThat(run.status()).isEqualTo(RunStatus.FAILED);
        assertThat(run.failure().message()).contains("rejected by slack:U0APPROVER1");
        assertThat(channel.shown().getLast().outcome().decision()).isEqualTo(Decision.rejected);
    }

    @Test
    void secondClickChangesNothingAndShowsTheFirstDecision() {
        clicks.apply(click(Decision.approved, 5, "U0APPROVER1"));

        clicks.apply(click(Decision.rejected, 5, "U0SOMEONE02"));

        var run = runs.find(runId).orElseThrow();
        assertThat(run.status()).isEqualTo(RunStatus.QUEUED);
        var shown = channel.shown().getLast().outcome();
        assertThat(shown.decision()).isEqualTo(Decision.approved);
        assertThat(shown.decidedBy()).isEqualTo("slack:U0APPROVER1");
    }

    @Test
    void clickOnAnotherStepChangesNothing() {
        clicks.apply(click(Decision.approved, 3, "U0APPROVER1"));

        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.WAITING_APPROVAL);
        var shown = channel.shown().getLast();
        assertThat(shown.request().tool()).isNull();
        assertThat(shown.outcome().note()).contains("No longer waiting", "WAITING_APPROVAL");
    }

    @Test
    void unknownRunIsIgnored() {
        clicks.apply(new SlackInteractions.Click("run_missing", 5, Decision.approved, "U0APPROVER1", MESSAGE));

        assertThat(channel.shown()).isEmpty();
    }

    @Test
    void failedMessageUpdateKeepsTheDecision() {
        channel.failNext(1);

        clicks.apply(click(Decision.approved, 5, "U0APPROVER1"));

        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
        assertThat(channel.shown()).isEmpty();
    }

    @Test
    void clickOnAnExpiredRunChangesNothingAndShowsTheExpiry() {
        var failure = dev.agentplatform.controlplane.run.ApprovalDecision.expiredFailure(PENDING,
                java.time.Duration.ofHours(72));
        runs.expireApproval(runId, PENDING, failure, java.time.Instant.now());

        clicks.apply(click(Decision.approved, 5, "U0APPROVER1"));

        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.FAILED);
        assertThat(queue.bodies()).hasSize(1); // only the start
        assertThat(channel.shown().getLast().outcome().note())
                .isEqualTo("Expired: Call to github.close_issue at step 5 got no decision within 72 h");
    }
}
