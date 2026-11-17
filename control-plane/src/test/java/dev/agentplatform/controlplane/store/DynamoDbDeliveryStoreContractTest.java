package dev.agentplatform.controlplane.store;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbDeliveryStoreContractTest extends DeliveryStoreContractTest {

    @Override
    protected DeliveryStore newStore() {
        return new DynamoDbDeliveryStore(DynamoDbLocal.client(), DynamoDbLocal.newTable());
    }
}
