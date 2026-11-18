package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.approval.ApprovalNotifier;
import dev.agentplatform.controlplane.approval.InMemoryApprovalChannel;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import java.time.Clock;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

class RunServiceTest {

    @Test
    void runStaysQueuedWhenEnqueueFails() {
        var agents = new InMemoryAgentStore(Clock.systemUTC());
        agents.createAgent("t1", "triage");
        agents.publishVersion("t1", "triage", "github.issue.opened", "{}");
        var runs = new InMemoryRunStore();
        var attempted = new AtomicReference<String>();
        var service = new RunService(agents, runs, message -> {
            attempted.set(message.runId());
            throw new RunQueueException("queue down", null);
        }, new ApprovalNotifier(new InMemoryApprovalChannel(), runs), Clock.systemUTC());

        assertThatThrownBy(() -> service.start("t1", "triage", "{}")).isInstanceOf(RunQueueException.class);

        // The run was written before the enqueue attempt and is left for the re-enqueue sweep.
        assertThat(runs.find(attempted.get()).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void runIdsAreUniqueAndWellFormed() {
        var a = RunService.newRunId();
        var b = RunService.newRunId();

        assertThat(a).matches("^run_[0-9a-f]{32}$").isNotEqualTo(b);
    }

    @Test
    void approvalStandsWhenEnqueueFails() {
        var agents = new InMemoryAgentStore(Clock.systemUTC());
        agents.createAgent("t1", "triage");
        agents.publishVersion("t1", "triage", "github.issue.opened", "{}");
        var runs = new InMemoryRunStore();
        var down = new java.util.concurrent.atomic.AtomicBoolean(false);
        var service = new RunService(agents, runs, message -> {
            if (down.get()) {
                throw new RunQueueException("queue down", null);
            }
        }, new ApprovalNotifier(new InMemoryApprovalChannel(), runs), Clock.systemUTC());
        var runId = service.start("t1", "triage", "{}").runId();
        service.update(runId, new RunUpdate(RunStatus.RUNNING, null, null, null, null, null, null));
        service.update(runId, new RunUpdate(RunStatus.WAITING_APPROVAL, null, null, null,
                "{\"step\":3,\"tool\":\"github.close_issue\",\"arguments\":{}}", null, null));
        down.set(true);

        var decided = service.decideApproval("t1", runId, 3, ApprovalDecision.Decision.approved, "api:t1");

        // No error for the caller: the decision is saved and the sweep sends the QUEUED run.
        assertThat(decided.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(runs.find(runId).orElseThrow().approvalJson()).contains("\"decision\":\"approved\"");
    }
}
