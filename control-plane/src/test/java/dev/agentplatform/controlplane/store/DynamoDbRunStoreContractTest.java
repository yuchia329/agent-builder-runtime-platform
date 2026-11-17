package dev.agentplatform.controlplane.store;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbRunStoreContractTest extends RunStoreContractTest {

    @Override
    protected RunStore newStore() {
        return new DynamoDbRunStore(DynamoDbLocal.client(), DynamoDbLocal.newTable());
    }
}
