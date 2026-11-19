package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Clock;
import java.util.Map;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbAgentStoreContractTest extends AgentStoreContractTest {

    private String table;

    @Override
    protected AgentStore newStore() {
        table = DynamoDbLocal.newTable();
        return new DynamoDbAgentStore(DynamoDbLocal.client(), table, Clock.systemUTC());
    }

    @Test
    void versionWrittenBeforeOwnerReadsAsTheTenantsKey() {
        var store = newStore();
        store.createAgent("t1", "triage");
        store.publishVersion("t1", "triage", "github.issue.opened", "{}", "user:42");
        DynamoDbLocal.client().updateItem(r -> r.tableName(table)
                .key(Map.of("PK", AttributeValue.fromS(DynamoDbAgentStore.agentPk("t1", "triage")),
                        "SK", AttributeValue.fromS(DynamoDbAgentStore.versionSk(1))))
                .updateExpression("REMOVE #owner")
                .expressionAttributeNames(Map.of("#owner", "owner")));

        assertThat(store.findVersion("t1", "triage", 1).orElseThrow().owner()).isEqualTo("api:t1");
    }
}
