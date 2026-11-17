package dev.agentplatform.controlplane.store;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.ReturnValuesOnConditionCheckFailure;

/**
 * {@link DeliveryStore} on the control-plane table. Item layout:
 *
 * <pre>
 * Delivery  PK = DELIVERY#&lt;source&gt;#&lt;id&gt;  SK = META
 *           runIds (list), ttl (epoch seconds, claim + RETENTION)
 * </pre>
 *
 * DynamoDB TTL ({@code dynamodb/control-plane-ttl.json}) deletes expired items in the background,
 * days later at worst, so the claim also treats an item whose {@code ttl} has passed as absent.
 */
public class DynamoDbDeliveryStore implements DeliveryStore {

    private final DynamoDbClient dynamo;
    private final String table;

    public DynamoDbDeliveryStore(DynamoDbClient dynamo, String table) {
        this.dynamo = dynamo;
        this.table = table;
    }

    @Override
    public Optional<List<String>> claim(String source, String deliveryId, Instant now) {
        var item = Map.of(
                "PK", s(pk(source, deliveryId)),
                "SK", s("META"),
                "runIds", AttributeValue.fromL(List.of()),
                "ttl", n(now.plus(RETENTION).getEpochSecond()));
        try {
            dynamo.putItem(r -> r.tableName(table).item(item)
                    .conditionExpression("attribute_not_exists(PK) OR #ttl < :now")
                    .expressionAttributeNames(Map.of("#ttl", "ttl"))
                    .expressionAttributeValues(Map.of(":now", n(now.getEpochSecond())))
                    .returnValuesOnConditionCheckFailure(ReturnValuesOnConditionCheckFailure.ALL_OLD));
            return Optional.empty();
        } catch (ConditionalCheckFailedException e) {
            var runIds = e.hasItem() && e.item().containsKey("runIds")
                    ? e.item().get("runIds").l().stream().map(AttributeValue::s).toList()
                    : List.<String>of();
            return Optional.of(runIds);
        }
    }

    @Override
    public void recordRuns(String source, String deliveryId, List<String> runIds) {
        var ids = runIds.stream().map(DynamoDbDeliveryStore::s).toList();
        dynamo.updateItem(r -> r.tableName(table).key(key(source, deliveryId))
                .updateExpression("SET runIds = :ids")
                .conditionExpression("attribute_exists(PK)")
                .expressionAttributeValues(Map.of(":ids", AttributeValue.fromL(ids))));
    }

    @Override
    public void release(String source, String deliveryId) {
        dynamo.deleteItem(r -> r.tableName(table).key(key(source, deliveryId)));
    }

    static String pk(String source, String deliveryId) {
        return "DELIVERY#" + source + "#" + deliveryId;
    }

    private static Map<String, AttributeValue> key(String source, String deliveryId) {
        return Map.of("PK", s(pk(source, deliveryId)), "SK", s("META"));
    }

    private static AttributeValue s(String value) {
        return AttributeValue.fromS(value);
    }

    private static AttributeValue n(long value) {
        return AttributeValue.fromN(Long.toString(value));
    }
}
