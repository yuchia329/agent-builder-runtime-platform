package dev.agentplatform.controlplane.store;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbTenantStoreContractTest extends TenantStoreContractTest {

    @Override
    protected TenantStore newStore() {
        return new DynamoDbTenantStore(DynamoDbLocal.client(), DynamoDbLocal.newTable());
    }
}
