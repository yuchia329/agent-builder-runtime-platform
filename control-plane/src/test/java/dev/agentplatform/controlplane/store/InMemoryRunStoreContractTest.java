package dev.agentplatform.controlplane.store;

class InMemoryRunStoreContractTest extends RunStoreContractTest {

    @Override
    protected RunStore newStore() {
        return new InMemoryRunStore();
    }
}
