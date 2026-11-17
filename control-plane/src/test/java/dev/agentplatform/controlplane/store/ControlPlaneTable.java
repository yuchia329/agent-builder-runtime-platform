package dev.agentplatform.controlplane.store;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeDefinition;
import software.amazon.awssdk.services.dynamodb.model.BillingMode;
import software.amazon.awssdk.services.dynamodb.model.CreateTableRequest;
import software.amazon.awssdk.services.dynamodb.model.GlobalSecondaryIndex;
import software.amazon.awssdk.services.dynamodb.model.KeySchemaElement;
import software.amazon.awssdk.services.dynamodb.model.Projection;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Creates a table from {@code dynamodb/control-plane-table.json}, the single definition of the
 * control-plane table's keys and indexes (also read by local-stack and infra).
 */
final class ControlPlaneTable {

    static final Path DEFINITION = Path.of("dynamodb", "control-plane-table.json");

    private ControlPlaneTable() {}

    static void create(DynamoDbClient client, String tableName) {
        JsonNode def = JsonMapper.builder().build().readTree(DEFINITION.toFile());

        var attributes = new ArrayList<AttributeDefinition>();
        def.get("AttributeDefinitions").forEach(a -> attributes.add(AttributeDefinition.builder()
                .attributeName(a.get("AttributeName").asString())
                .attributeType(a.get("AttributeType").asString())
                .build()));

        var indexes = new ArrayList<GlobalSecondaryIndex>();
        def.get("GlobalSecondaryIndexes").forEach(i -> indexes.add(GlobalSecondaryIndex.builder()
                .indexName(i.get("IndexName").asString())
                .keySchema(keySchema(i.get("KeySchema")))
                .projection(Projection.builder()
                        .projectionType(i.get("Projection").get("ProjectionType").asString()).build())
                .build()));

        client.createTable(CreateTableRequest.builder()
                .tableName(tableName)
                .billingMode(BillingMode.fromValue(def.get("BillingMode").asString()))
                .attributeDefinitions(attributes)
                .keySchema(keySchema(def.get("KeySchema")))
                .globalSecondaryIndexes(indexes)
                .build());
    }

    private static List<KeySchemaElement> keySchema(JsonNode keys) {
        var result = new ArrayList<KeySchemaElement>();
        keys.forEach(k -> result.add(KeySchemaElement.builder()
                .attributeName(k.get("AttributeName").asString())
                .keyType(k.get("KeyType").asString())
                .build()));
        return result;
    }
}
