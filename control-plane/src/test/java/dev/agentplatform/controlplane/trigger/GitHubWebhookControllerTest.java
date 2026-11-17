package dev.agentplatform.controlplane.trigger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.agent.Agent;
import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import dev.agentplatform.controlplane.store.InMemoryDeliveryStore;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

/** Delivery claims when handling fails partway, without HTTP. */
class GitHubWebhookControllerTest {

    private static final String SECRET = "s3cret";
    private static final String BODY = "{\"action\":\"opened\",\"issue\":{\"number\":1}}";
    private static final PlatformProperties PROPS = new PlatformProperties(
            PlatformProperties.StoreBackend.MEMORY, "t", PlatformProperties.QueueBackend.MEMORY, null,
            new PlatformProperties.TenantProperties("t1", "key"), new PlatformProperties.GitHubProperties(SECRET), null);

    private final Clock clock = Clock.systemUTC();
    private final InMemoryRunStore runs = new InMemoryRunStore();
    private final InMemoryDeliveryStore deliveries = new InMemoryDeliveryStore();

    private GitHubWebhookController controller(InMemoryAgentStore agents, RunQueuePublisher queue) {
        agents.createAgent("t1", "triage");
        agents.publishVersion("t1", "triage", "github.issue.opened", "{}");
        return new GitHubWebhookController(new RunService(agents, runs, queue, clock), deliveries, clock, PROPS);
    }

    private static GitHubWebhookController.Started deliver(GitHubWebhookController c, String delivery) {
        var body = BODY.getBytes(StandardCharsets.UTF_8);
        return c.receive("issues", delivery, GitHubWebhooks.sign(SECRET, body), body).getBody();
    }

    @Test
    void failureBeforeAnyRunIsSavedReleasesTheClaimSoARedeliveryWorks() {
        var failures = new AtomicInteger(1);
        var agents = new InMemoryAgentStore(clock) {
            @Override
            public List<Agent> findAgentsByTrigger(String trigger) {
                if (failures.getAndDecrement() > 0) {
                    throw new IllegalStateException("store unavailable");
                }
                return super.findAgentsByTrigger(trigger);
            }
        };
        var c = controller(agents, message -> {});

        assertThatThrownBy(() -> deliver(c, "d-1")).hasMessageContaining("store unavailable");
        var retry = deliver(c, "d-1");

        assertThat(retry.runs()).hasSize(1);
        assertThat(retry.duplicate()).isNull();
    }

    @Test
    void enqueueFailureKeepsTheClaimBecauseTheRunIsSavedForTheSweep() {
        var c = controller(new InMemoryAgentStore(clock), message -> {
            throw new RunQueueException("queue down", null);
        });

        assertThatThrownBy(() -> deliver(c, "d-2")).isInstanceOf(RunQueueException.class);
        var redelivery = deliver(c, "d-2");

        assertThat(redelivery.duplicate()).isTrue();
        assertThat(runs.findQueuedBefore(clock.instant().plusSeconds(1), 10))
                .hasSize(1)
                .allSatisfy(run -> assertThat(run.status()).isEqualTo(RunStatus.QUEUED));
    }
}
