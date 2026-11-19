package dev.agentplatform.controlplane.store;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.run.Run;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbRunStoreContractTest extends RunStoreContractTest {

    private String table;

    @Override
    protected RunStore newStore() {
        table = DynamoDbLocal.newTable();
        return new DynamoDbRunStore(DynamoDbLocal.client(), table);
    }

    @Test
    void runWrittenBeforeStartedByReadsAsTheTenantsKey() {
        var store = (DynamoDbRunStore) newStore();
        store.create(Run.queued("run_legacy", "t1", "triage", 1, "{}", "webhook:github", Instant.now()));
        DynamoDbLocal.client().updateItem(r -> r.tableName(table)
                .key(Map.of("PK", AttributeValue.fromS("RUN#run_legacy"), "SK", AttributeValue.fromS("META")))
                .updateExpression("REMOVE startedBy"));

        assertThat(store.find("run_legacy").orElseThrow().startedBy()).isEqualTo("api:t1");
    }
}
