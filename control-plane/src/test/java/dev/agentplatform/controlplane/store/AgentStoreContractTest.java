package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.agent.Agent;
import dev.agentplatform.controlplane.agent.AgentVersion;
import java.util.ArrayList;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Behavior every {@link AgentStore} adapter must have (ADR 0004). Each adapter gets a subclass
 * that supplies a fresh, empty store.
 */
abstract class AgentStoreContractTest {

    private static final String SPEC_V1 = "{\"name\":\"triage\",\"trigger\":\"github.issue.opened\"}";
    private static final String SPEC_V2 = "{\"name\":\"triage\",\"trigger\":\"github.issue.labeled\"}";

    protected AgentStore store;

    protected abstract AgentStore newStore();

    @BeforeEach
    void setUp() {
        store = newStore();
    }

    @Test
    void createdAgentHasNoVersionsYet() {
        store.createAgent("t1", "triage");

        var agent = store.findAgent("t1", "triage").orElseThrow();
        assertThat(agent.tenantId()).isEqualTo("t1");
        assertThat(agent.name()).isEqualTo("triage");
        assertThat(agent.latestVersion()).isZero();
        assertThat(agent.activeVersion()).isNull();
        assertThat(agent.trigger()).isNull();
        assertThat(agent.createdAt()).isNotNull();
    }

    @Test
    void missingAgentIsEmpty() {
        assertThat(store.findAgent("t1", "nope")).isEmpty();
    }

    @Test
    void agentNameIsUniquePerTenant() {
        store.createAgent("t1", "triage");

        assertThatThrownBy(() -> store.createAgent("t1", "triage"))
                .isInstanceOf(AgentAlreadyExistsException.class);
    }

    @Test
    void tenantsAreIsolated() {
        store.createAgent("t1", "triage");
        store.createAgent("t2", "triage");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1);

        assertThat(store.findAgent("t2", "triage").orElseThrow().latestVersion()).isZero();
        assertThat(store.findVersion("t2", "triage", 1)).isEmpty();
        assertThat(store.findAgent("t3", "triage")).isEmpty();
    }

    @Test
    void publishingToMissingAgentFails() {
        assertThatThrownBy(() -> store.publishVersion("t1", "nope", "github.issue.opened", SPEC_V1))
                .isInstanceOf(AgentNotFoundException.class);
    }

    @Test
    void versionsAreNumberedFromOneAndLatestIsActive() {
        store.createAgent("t1", "triage");

        var numbers = IntStream.range(0, 3)
                .map(i -> store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1).version())
                .toArray();

        assertThat(numbers).containsExactly(1, 2, 3);
        var agent = store.findAgent("t1", "triage").orElseThrow();
        assertThat(agent.latestVersion()).isEqualTo(3);
        assertThat(agent.activeVersion()).isEqualTo(3);
        assertThat(agent.trigger()).isEqualTo("github.issue.opened");
    }

    @Test
    void versionRecordsWhoPublishedIt() {
        store.createAgent("t1", "triage");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1, "user:42");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1);

        assertThat(store.findVersion("t1", "triage", 1).orElseThrow().owner()).isEqualTo("user:42");
        assertThat(store.findVersion("t1", "triage", 2).orElseThrow().owner()).isEqualTo("api:t1");
    }

    @Test
    void publishedVersionIsStoredUnchanged() {
        store.createAgent("t1", "triage");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1);
        store.publishVersion("t1", "triage", "github.issue.labeled", SPEC_V2);

        AgentVersion v1 = store.findVersion("t1", "triage", 1).orElseThrow();
        assertThat(v1.version()).isEqualTo(1);
        assertThat(v1.trigger()).isEqualTo("github.issue.opened");
        assertThat(v1.specJson()).isEqualTo(SPEC_V1);
        assertThat(store.findVersion("t1", "triage", 2).orElseThrow().specJson()).isEqualTo(SPEC_V2);
        assertThat(store.findVersion("t1", "triage", 3)).isEmpty();
    }

    @Test
    void agentsAreFoundByActiveTriggerAcrossTenants() {
        store.createAgent("t1", "triage");
        store.createAgent("t2", "triage");
        store.createAgent("t1", "labeler");
        store.createAgent("t1", "unpublished");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1);
        store.publishVersion("t2", "triage", "github.issue.opened", SPEC_V1);
        store.publishVersion("t1", "labeler", "github.issue.labeled", SPEC_V2);

        assertThat(store.findAgentsByTrigger("github.issue.opened"))
                .extracting(Agent::tenantId, Agent::name)
                .containsExactlyInAnyOrder(
                        org.assertj.core.groups.Tuple.tuple("t1", "triage"),
                        org.assertj.core.groups.Tuple.tuple("t2", "triage"));
        assertThat(store.findAgentsByTrigger("github.pr.opened")).isEmpty();
    }

    @Test
    void newVersionMovesAgentToItsTrigger() {
        store.createAgent("t1", "triage");
        store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1);
        store.publishVersion("t1", "triage", "github.issue.labeled", SPEC_V2);

        assertThat(store.findAgentsByTrigger("github.issue.opened")).isEmpty();
        assertThat(store.findAgentsByTrigger("github.issue.labeled"))
                .extracting(Agent::name).containsExactly("triage");
    }

    @Test
    void concurrentPublishesGetDistinctGapFreeNumbers() throws Exception {
        store.createAgent("t1", "triage");
        int publishers = 10;
        var start = new CountDownLatch(1);
        var tasks = new ArrayList<Callable<Integer>>();
        for (int i = 0; i < publishers; i++) {
            tasks.add(() -> {
                start.await();
                return store.publishVersion("t1", "triage", "github.issue.opened", SPEC_V1).version();
            });
        }

        var numbers = new ArrayList<Integer>();
        try (var pool = Executors.newFixedThreadPool(publishers)) {
            var futures = tasks.stream().map(pool::submit).toList();
            start.countDown();
            for (var future : futures) {
                numbers.add(future.get());
            }
        }

        assertThat(numbers).containsExactlyInAnyOrderElementsOf(IntStream.rangeClosed(1, publishers).boxed().toList());
        assertThat(store.findAgent("t1", "triage").orElseThrow().latestVersion()).isEqualTo(publishers);
    }
}
