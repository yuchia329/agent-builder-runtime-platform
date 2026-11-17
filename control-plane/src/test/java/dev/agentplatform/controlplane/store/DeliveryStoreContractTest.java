package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** Behavior every {@link DeliveryStore} adapter must have. */
abstract class DeliveryStoreContractTest {

    private static final Instant T0 = Instant.parse("2026-11-17T10:00:00Z");

    protected DeliveryStore store;
    private String id;

    protected abstract DeliveryStore newStore();

    @BeforeEach
    void setUp() {
        store = newStore();
        id = UUID.randomUUID().toString();
    }

    @Test
    void firstClaimWinsAndLaterClaimsSeeTheRecordedRuns() {
        assertThat(store.claim("github", id, T0)).isEmpty();
        assertThat(store.claim("github", id, T0)).contains(List.of()); // still working
        store.recordRuns("github", id, List.of("run_a", "run_b"));

        assertThat(store.claim("github", id, T0.plusSeconds(60))).contains(List.of("run_a", "run_b"));
    }

    @Test
    void sourcesAndIdsAreSeparate() {
        store.claim("github", id, T0);

        assertThat(store.claim("slack", id, T0)).isEmpty();
        assertThat(store.claim("github", id + "-other", T0)).isEmpty();
    }

    @Test
    void releasedClaimCanBeMadeAgain() {
        store.claim("github", id, T0);

        store.release("github", id);

        assertThat(store.claim("github", id, T0)).isEmpty();
    }

    @Test
    void claimIsForgottenAfterTheRetention() {
        store.claim("github", id, T0);

        assertThat(store.claim("github", id, T0.plus(DeliveryStore.RETENTION).minusSeconds(1))).isPresent();
        assertThat(store.claim("github", id, T0.plus(DeliveryStore.RETENTION).plusSeconds(1))).isEmpty();
    }

    @Test
    void concurrentClaimsExactlyOneWins() throws Exception {
        int threads = 8;
        var ready = new CountDownLatch(threads);
        var go = new CountDownLatch(1);
        var tasks = new ArrayList<Callable<Boolean>>();
        for (int i = 0; i < threads; i++) {
            tasks.add(() -> {
                ready.countDown();
                go.await();
                return store.claim("github", id, T0).isEmpty();
            });
        }
        try (var pool = Executors.newFixedThreadPool(threads)) {
            var futures = tasks.stream().map(pool::submit).toList();
            ready.await();
            go.countDown();
            long wins = 0;
            for (var f : futures) {
                if (f.get()) {
                    wins++;
                }
            }
            assertThat(wins).isEqualTo(1);
        }
    }
}
