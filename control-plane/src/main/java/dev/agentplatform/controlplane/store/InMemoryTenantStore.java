package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.tenant.Tenant;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/** {@link TenantStore} for tests and local runs without DynamoDB. Thread-safe; lost on exit. */
public class InMemoryTenantStore implements TenantStore {

    private final Map<String, Tenant> tenants = new HashMap<>();
    private final Map<String, String> keyHashes = new HashMap<>();

    @Override
    public synchronized void create(Tenant tenant) {
        if (tenants.putIfAbsent(tenant.id(), tenant) != null) {
            throw new TenantAlreadyExistsException(tenant.id());
        }
    }

    @Override
    public synchronized Optional<Tenant> find(String tenantId) {
        return Optional.ofNullable(tenants.get(tenantId));
    }

    @Override
    public synchronized void setLimits(String tenantId, TenantLimits limits) {
        var tenant = tenants.get(tenantId);
        if (tenant == null) {
            throw new TenantNotFoundException(tenantId);
        }
        tenants.put(tenantId, new Tenant(tenantId, limits, tenant.createdAt()));
    }

    @Override
    public synchronized void addApiKey(String tenantId, String keyHash, String keyPrefix, Instant now) {
        if (!tenants.containsKey(tenantId)) {
            throw new TenantNotFoundException(tenantId);
        }
        if (keyHashes.putIfAbsent(keyHash, tenantId) != null) {
            throw new IllegalStateException("API key hash already stored (prefix " + keyPrefix + ")");
        }
    }

    @Override
    public synchronized Optional<String> tenantForKey(String keyHash) {
        return Optional.ofNullable(keyHashes.get(keyHash));
    }
}
