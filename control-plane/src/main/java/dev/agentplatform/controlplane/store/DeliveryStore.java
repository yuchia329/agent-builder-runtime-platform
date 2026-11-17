package dev.agentplatform.controlplane.store;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * Port for webhook deliveries already handled, so a redelivered event starts no new runs. Every
 * adapter must pass {@code DeliveryStoreContractTest}. A delivery is remembered for
 * {@link #RETENTION} after its claim; after that the same id counts as new.
 */
public interface DeliveryStore {

    /** Covers GitHub's 3-day redelivery window with margin. */
    Duration RETENTION = Duration.ofDays(7);

    /**
     * Claims a delivery atomically: of concurrent callers with the same id, exactly one sees empty.
     *
     * @return empty if this call made the claim (the caller handles the delivery); otherwise the run
     *     ids recorded by the first claimer (empty list while it is still working)
     */
    Optional<List<String>> claim(String source, String deliveryId, Instant now);

    /** Records the runs a claimed delivery started. */
    void recordRuns(String source, String deliveryId, List<String> runIds);

    /** Forgets a claim, so the delivery can be handled again (used when handling failed). */
    void release(String source, String deliveryId);
}
