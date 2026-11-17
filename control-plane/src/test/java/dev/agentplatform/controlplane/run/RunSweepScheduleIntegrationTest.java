package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.RunStore;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** The roadmap check: a run saved QUEUED whose enqueue never happened reaches the queue. */
@SpringBootTest(properties = {
        "platform.tenant.api-key=test-key",
        "platform.sweep.interval-seconds=1",
        "platform.sweep.queued-after-seconds=0"})
class RunSweepScheduleIntegrationTest {

    @Autowired
    RunStore runs;

    @Autowired
    RunQueuePublisher publisher;

    @Test
    void runWhoseEnqueueFailedReachesTheQueueOnTheNextSweep() throws Exception {
        var run = Run.queued("run_" + "a".repeat(32), "tenant-demo", "triage", 1, "{}", Instant.now().minusSeconds(5));
        runs.create(run); // saved, never enqueued: what a failed enqueue leaves behind

        var queue = (InMemoryRunQueuePublisher) publisher;
        var deadline = System.nanoTime() + 5_000_000_000L;
        while (queue.bodies().stream().noneMatch(b -> b.contains(run.runId())) && System.nanoTime() < deadline) {
            Thread.sleep(100); // bodies() is a snapshot: read it again
        }

        assertThat(queue.bodies()).anyMatch(b -> b.contains("\"run_id\":\"" + run.runId() + "\""));
    }
}
