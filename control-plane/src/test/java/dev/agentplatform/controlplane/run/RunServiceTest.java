package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
        }, Clock.systemUTC());

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
}
