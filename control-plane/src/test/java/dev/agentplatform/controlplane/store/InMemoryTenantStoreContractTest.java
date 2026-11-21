package dev.agentplatform.controlplane.store;

class InMemoryTenantStoreContractTest extends TenantStoreContractTest {

    @Override
    protected TenantStore newStore() {
        return new InMemoryTenantStore();
    }
}
