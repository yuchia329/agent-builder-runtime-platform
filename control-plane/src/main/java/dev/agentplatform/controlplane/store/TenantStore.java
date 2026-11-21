package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.tenant.Tenant;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.time.Instant;
import java.util.Optional;

/**
 * Port for tenants and their API keys (ADR 0026). Keys are stored only as hashes
 * ({@code tenant/ApiKeys}). Every adapter must pass {@code TenantStoreContractTest}.
 */
public interface TenantStore {

    /** @throws TenantAlreadyExistsException if a tenant with this id exists */
    void create(Tenant tenant);

    Optional<Tenant> find(String tenantId);

    /** @throws TenantNotFoundException if the tenant does not exist */
    void setLimits(String tenantId, TenantLimits limits);

    /**
     * Adds a key (its hash and prefix) to an existing tenant.
     *
     * @throws TenantNotFoundException if the tenant does not exist
     * @throws IllegalStateException if a key with this hash exists (a 256-bit key never repeats)
     */
    void addApiKey(String tenantId, String keyHash, String keyPrefix, Instant now);

    /** The tenant a key hash belongs to, empty if unknown. */
    Optional<String> tenantForKey(String keyHash);

    final class TenantAlreadyExistsException extends RuntimeException {
        public TenantAlreadyExistsException(String tenantId) {
            super("Tenant already exists: " + tenantId);
        }
    }

    final class TenantNotFoundException extends RuntimeException {
        public TenantNotFoundException(String tenantId) {
            super("Tenant not found: " + tenantId);
        }
    }
}
