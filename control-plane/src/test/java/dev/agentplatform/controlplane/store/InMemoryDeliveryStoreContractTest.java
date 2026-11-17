package dev.agentplatform.controlplane.store;

class InMemoryDeliveryStoreContractTest extends DeliveryStoreContractTest {

    @Override
    protected DeliveryStore newStore() {
        return new InMemoryDeliveryStore();
    }
}
