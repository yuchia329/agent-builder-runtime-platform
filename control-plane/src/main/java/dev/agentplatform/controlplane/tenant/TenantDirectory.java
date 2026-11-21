package dev.agentplatform.controlplane.tenant;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.store.TenantStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import org.springframework.stereotype.Component;

/**
 * A tenant's limits: its stored record, or the defaults from settings ({@code TENANT_MAX_RUNNING},
 * {@code TENANT_DAILY_USD}) for a tenant without one, such as the configured first tenant before a
 * record is made for it.
 */
@Component
public class TenantDirectory {

    private final TenantStore store;
    private final TenantLimits defaults;

    public TenantDirectory(TenantStore store, PlatformProperties props) {
        this.store = store;
        this.defaults = new TenantLimits(props.tenant().maxRunning(), props.tenant().dailyUsd());
    }

    public TenantLimits limits(String tenantId) {
        return store.find(tenantId).map(Tenant::limits).orElse(defaults);
    }

    public TenantLimits defaults() {
        return defaults;
    }
}
