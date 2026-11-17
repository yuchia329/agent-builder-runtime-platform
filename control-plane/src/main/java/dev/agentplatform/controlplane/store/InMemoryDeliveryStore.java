package dev.agentplatform.controlplane.store;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** {@link DeliveryStore} for tests and local runs without DynamoDB. Thread-safe; lost on exit. */
public class InMemoryDeliveryStore implements DeliveryStore {

    private record Claim(Instant expiresAt, List<String> runIds) {}

    private final Map<String, Claim> claims = new HashMap<>();

    @Override
    public synchronized Optional<List<String>> claim(String source, String deliveryId, Instant now) {
        var key = source + "#" + deliveryId;
        var existing = claims.get(key);
        if (existing != null && existing.expiresAt().isAfter(now)) {
            return Optional.of(existing.runIds());
        }
        claims.put(key, new Claim(now.plus(RETENTION), List.of()));
        return Optional.empty();
    }

    @Override
    public synchronized void recordRuns(String source, String deliveryId, List<String> runIds) {
        claims.computeIfPresent(source + "#" + deliveryId, (k, c) -> new Claim(c.expiresAt(), List.copyOf(runIds)));
    }

    @Override
    public synchronized void release(String source, String deliveryId) {
        claims.remove(source + "#" + deliveryId);
    }
}
