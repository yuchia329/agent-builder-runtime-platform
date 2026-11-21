package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.tenant.ApiKeys;
import dev.agentplatform.controlplane.tenant.Tenant;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** Behavior every {@link TenantStore} adapter must have. */
abstract class TenantStoreContractTest {

    private static final Instant T0 = Instant.parse("2026-11-21T10:00:00Z");
    private static final TenantLimits LIMITS = new TenantLimits(50, new BigDecimal("5.25"));

    protected TenantStore store;
    private String id;

    protected abstract TenantStore newStore();

    @BeforeEach
    void setUp() {
        store = newStore();
        id = "t-" + UUID.randomUUID().toString().substring(0, 8);
    }

    @Test
    void createdTenantIsFoundWithItsLimits() {
        store.create(new Tenant(id, LIMITS, T0));

        var found = store.find(id).orElseThrow();
        assertThat(found.limits().maxRunning()).isEqualTo(50);
        assertThat(found.limits().dailyUsd()).isEqualByComparingTo("5.25");
        assertThat(found.createdAt()).isEqualTo(T0);
    }

    @Test
    void unknownTenantIsEmpty() {
        assertThat(store.find(id)).isEmpty();
    }

    @Test
    void aTenantIsCreatedOnce() {
        store.create(new Tenant(id, LIMITS, T0));

        assertThatThrownBy(() -> store.create(new Tenant(id, new TenantLimits(1, BigDecimal.ONE), T0)))
                .isInstanceOf(TenantStore.TenantAlreadyExistsException.class);
        assertThat(store.find(id).orElseThrow().limits().maxRunning()).isEqualTo(50);
    }

    @Test
    void limitsCanBeChanged() {
        store.create(new Tenant(id, LIMITS, T0));

        store.setLimits(id, new TenantLimits(3, new BigDecimal("0.5")));

        var limits = store.find(id).orElseThrow().limits();
        assertThat(limits.maxRunning()).isEqualTo(3);
        assertThat(limits.dailyUsd()).isEqualByComparingTo("0.5");
    }

    @Test
    void limitsOfAnUnknownTenantFail() {
        assertThatThrownBy(() -> store.setLimits(id, LIMITS)).isInstanceOf(TenantStore.TenantNotFoundException.class);
        assertThat(store.find(id)).isEmpty();
    }

    @Test
    void aKeyNamesItsTenant() {
        store.create(new Tenant(id, LIMITS, T0));
        var key = ApiKeys.generate();

        store.addApiKey(id, key.hash(), key.prefix(), T0);

        assertThat(store.tenantForKey(key.hash())).contains(id);
        assertThat(store.tenantForKey(ApiKeys.generate().hash())).isEmpty();
    }

    @Test
    void aTenantCanHaveSeveralKeys() {
        store.create(new Tenant(id, LIMITS, T0));
        var first = ApiKeys.generate();
        var second = ApiKeys.generate();

        store.addApiKey(id, first.hash(), first.prefix(), T0);
        store.addApiKey(id, second.hash(), second.prefix(), T0);

        assertThat(store.tenantForKey(first.hash())).contains(id);
        assertThat(store.tenantForKey(second.hash())).contains(id);
    }

    @Test
    void aKeyNeedsAnExistingTenant() {
        var key = ApiKeys.generate();

        assertThatThrownBy(() -> store.addApiKey(id, key.hash(), key.prefix(), T0))
                .isInstanceOf(TenantStore.TenantNotFoundException.class);
        assertThat(store.tenantForKey(key.hash())).isEmpty();
    }

    @Test
    void aKeyHashIsStoredOnce() {
        store.create(new Tenant(id, LIMITS, T0));
        var other = id + "-x";
        store.create(new Tenant(other, LIMITS, T0));
        var key = ApiKeys.generate();
        store.addApiKey(id, key.hash(), key.prefix(), T0);

        assertThatThrownBy(() -> store.addApiKey(other, key.hash(), key.prefix(), T0))
                .isInstanceOf(IllegalStateException.class);
        assertThat(store.tenantForKey(key.hash())).contains(id);
    }
}
