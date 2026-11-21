package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.tenant.Tenant;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.TransactWriteItem;
import software.amazon.awssdk.services.dynamodb.model.TransactionCanceledException;

/**
 * {@link TenantStore} on the control-plane table. Item layout:
 *
 * <pre>
 * Tenant   PK = TENANT#&lt;t&gt;         SK = META   maxRunning (N), dailyUsd (N), createdAt (S, ISO-8601)
 * API key  PK = APIKEY#&lt;sha256 hex&gt; SK = META   tenantId, keyPrefix, createdAt
 * </pre>
 *
 * {@code TENANT#<t>} never collides with an agent's {@code TENANT#<t>#AGENT#<a>}. A key is added in
 * one transaction with a check that its tenant exists.
 */
public class DynamoDbTenantStore implements TenantStore {

    private final DynamoDbClient dynamo;
    private final String table;

    public DynamoDbTenantStore(DynamoDbClient dynamo, String table) {
        this.dynamo = dynamo;
        this.table = table;
    }

    @Override
    public void create(Tenant tenant) {
        var item = Map.of(
                "PK", s(tenantPk(tenant.id())),
                "SK", s("META"),
                "maxRunning", n(Integer.toString(tenant.limits().maxRunning())),
                "dailyUsd", n(tenant.limits().dailyUsd().toPlainString()),
                "createdAt", s(tenant.createdAt().toString()));
        try {
            dynamo.putItem(r -> r.tableName(table).item(item).conditionExpression("attribute_not_exists(PK)"));
        } catch (ConditionalCheckFailedException e) {
            throw new TenantAlreadyExistsException(tenant.id());
        }
    }

    @Override
    public Optional<Tenant> find(String tenantId) {
        var item = dynamo.getItem(r -> r.tableName(table).key(key(tenantPk(tenantId))).consistentRead(true)).item();
        if (item == null || item.isEmpty()) {
            return Optional.empty();
        }
        var limits = new TenantLimits(Integer.parseInt(item.get("maxRunning").n()),
                new BigDecimal(item.get("dailyUsd").n()));
        return Optional.of(new Tenant(tenantId, limits, Instant.parse(item.get("createdAt").s())));
    }

    @Override
    public void setLimits(String tenantId, TenantLimits limits) {
        try {
            dynamo.updateItem(r -> r.tableName(table).key(key(tenantPk(tenantId)))
                    .updateExpression("SET maxRunning = :m, dailyUsd = :d")
                    .conditionExpression("attribute_exists(PK)")
                    .expressionAttributeValues(Map.of(
                            ":m", n(Integer.toString(limits.maxRunning())),
                            ":d", n(limits.dailyUsd().toPlainString()))));
        } catch (ConditionalCheckFailedException e) {
            throw new TenantNotFoundException(tenantId);
        }
    }

    @Override
    public void addApiKey(String tenantId, String keyHash, String keyPrefix, Instant now) {
        var tenantExists = TransactWriteItem.builder().conditionCheck(c -> c.tableName(table)
                .key(key(tenantPk(tenantId))).conditionExpression("attribute_exists(PK)")).build();
        var putKey = TransactWriteItem.builder().put(p -> p.tableName(table)
                .item(Map.of(
                        "PK", s(keyPk(keyHash)),
                        "SK", s("META"),
                        "tenantId", s(tenantId),
                        "keyPrefix", s(keyPrefix),
                        "createdAt", s(now.toString())))
                .conditionExpression("attribute_not_exists(PK)")).build();
        try {
            dynamo.transactWriteItems(r -> r.transactItems(tenantExists, putKey));
        } catch (TransactionCanceledException e) {
            var reasons = e.cancellationReasons();
            if (!reasons.isEmpty() && "ConditionalCheckFailed".equals(reasons.get(0).code())) {
                throw new TenantNotFoundException(tenantId);
            }
            throw new IllegalStateException("API key hash already stored (prefix " + keyPrefix + ")", e);
        }
    }

    @Override
    public Optional<String> tenantForKey(String keyHash) {
        var item = dynamo.getItem(r -> r.tableName(table).key(key(keyPk(keyHash)))).item();
        if (item == null || item.isEmpty()) {
            return Optional.empty();
        }
        return Optional.of(item.get("tenantId").s());
    }

    static String tenantPk(String tenantId) {
        return "TENANT#" + tenantId;
    }

    static String keyPk(String keyHash) {
        return "APIKEY#" + keyHash;
    }

    private static Map<String, AttributeValue> key(String pk) {
        return Map.of("PK", s(pk), "SK", s("META"));
    }

    private static AttributeValue s(String value) {
        return AttributeValue.fromS(value);
    }

    private static AttributeValue n(String value) {
        return AttributeValue.fromN(value);
    }
}
