package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.tenant.Principals;
import dev.agentplatform.controlplane.agent.Agent;
import dev.agentplatform.controlplane.agent.AgentVersion;
import java.time.Clock;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ThreadLocalRandom;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.Put;
import software.amazon.awssdk.services.dynamodb.model.TransactWriteItem;
import software.amazon.awssdk.services.dynamodb.model.TransactionCanceledException;
import software.amazon.awssdk.services.dynamodb.model.Update;

/**
 * {@link AgentStore} on the control-plane DynamoDB table (ADR 0008). Item layout:
 *
 * <pre>
 * Agent    PK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  SK = META
 *          latestVersion, activeVersion, trigger, createdAt
 *          GSI1PK = TRIGGER#&lt;trigger&gt;  GSI1SK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  (once published)
 * Version  PK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  SK = VERSION#&lt;10-digit number&gt;
 *          version, trigger, spec, createdAt
 * </pre>
 *
 * Publishing reads the agent's {@code latestVersion} n, then in one transaction sets it to n+1
 * only if it is still n and writes version n+1 only if it does not exist. A lost race retries,
 * so numbering has no gaps or repeats.
 */
public class DynamoDbAgentStore implements AgentStore {

    static final String GSI_TRIGGER = "GSI1";
    private static final int MAX_PUBLISH_ATTEMPTS = 25;

    private final DynamoDbClient dynamo;
    private final String table;
    private final Clock clock;

    public DynamoDbAgentStore(DynamoDbClient dynamo, String table, Clock clock) {
        this.dynamo = dynamo;
        this.table = table;
        this.clock = clock;
    }

    @Override
    public Agent createAgent(String tenantId, String name) {
        var now = clock.instant();
        var item = new HashMap<String, AttributeValue>();
        item.put("PK", s(agentPk(tenantId, name)));
        item.put("SK", s("META"));
        item.put("tenantId", s(tenantId));
        item.put("name", s(name));
        item.put("latestVersion", n(0));
        item.put("createdAt", s(now.toString()));
        try {
            dynamo.putItem(r -> r.tableName(table).item(item).conditionExpression("attribute_not_exists(PK)"));
        } catch (ConditionalCheckFailedException e) {
            throw new AgentAlreadyExistsException(tenantId, name);
        }
        return new Agent(tenantId, name, 0, null, null, now);
    }

    @Override
    public Optional<Agent> findAgent(String tenantId, String name) {
        var item = dynamo.getItem(r -> r.tableName(table).consistentRead(true)
                .key(key(agentPk(tenantId, name), "META"))).item();
        return item == null || item.isEmpty() ? Optional.empty() : Optional.of(toAgent(item));
    }

    @Override
    public AgentVersion publishVersion(String tenantId, String name, String trigger, String specJson, String owner) {
        var pk = agentPk(tenantId, name);
        for (int attempt = 1; attempt <= MAX_PUBLISH_ATTEMPTS; attempt++) {
            var current = findAgent(tenantId, name).orElseThrow(() -> new AgentNotFoundException(tenantId, name));
            int previous = current.latestVersion();
            int next = previous + 1;
            var now = clock.instant();

            var versionItem = new HashMap<String, AttributeValue>();
            versionItem.put("PK", s(pk));
            versionItem.put("SK", s(versionSk(next)));
            versionItem.put("version", n(next));
            versionItem.put("trigger", s(trigger));
            versionItem.put("spec", s(specJson));
            versionItem.put("owner", s(owner));
            versionItem.put("createdAt", s(now.toString()));

            var bumpAgent = Update.builder().tableName(table).key(key(pk, "META"))
                    .updateExpression("SET latestVersion = :next, activeVersion = :next, #trigger = :trigger, "
                            + "GSI1PK = :gsi1pk, GSI1SK = :pk")
                    .conditionExpression("latestVersion = :previous")
                    .expressionAttributeNames(Map.of("#trigger", "trigger"))
                    .expressionAttributeValues(Map.of(
                            ":next", n(next), ":previous", n(previous), ":trigger", s(trigger),
                            ":gsi1pk", s("TRIGGER#" + trigger), ":pk", s(pk)))
                    .build();
            var putVersion = Put.builder().tableName(table).item(versionItem)
                    .conditionExpression("attribute_not_exists(PK)").build();
            try {
                dynamo.transactWriteItems(r -> r.transactItems(
                        TransactWriteItem.builder().update(bumpAgent).build(),
                        TransactWriteItem.builder().put(putVersion).build()));
                return new AgentVersion(tenantId, name, next, trigger, specJson, owner, now);
            } catch (TransactionCanceledException e) {
                if (!isConditionFailure(e)) {
                    throw e;
                }
                backOff(attempt); // another publish took this number; read again and retry
            }
        }
        throw new IllegalStateException("Could not publish a version of '%s' after %d attempts"
                .formatted(name, MAX_PUBLISH_ATTEMPTS));
    }

    @Override
    public Optional<AgentVersion> findVersion(String tenantId, String name, int version) {
        var item = dynamo.getItem(r -> r.tableName(table)
                .key(key(agentPk(tenantId, name), versionSk(version)))).item();
        if (item == null || item.isEmpty()) {
            return Optional.empty();
        }
        return Optional.of(new AgentVersion(tenantId, name, Integer.parseInt(item.get("version").n()),
                item.get("trigger").s(), item.get("spec").s(),
                item.containsKey("owner") ? item.get("owner").s() : Principals.api(tenantId), // written before M4
                Instant.parse(item.get("createdAt").s())));
    }

    @Override
    public List<Agent> findAgentsByTrigger(String trigger) {
        return dynamo.queryPaginator(r -> r.tableName(table).indexName(GSI_TRIGGER)
                        .keyConditionExpression("GSI1PK = :pk")
                        .expressionAttributeValues(Map.of(":pk", s("TRIGGER#" + trigger))))
                .items().stream()
                .map(DynamoDbAgentStore::toAgent)
                .toList();
    }

    private static Agent toAgent(Map<String, AttributeValue> item) {
        var active = item.get("activeVersion");
        var trigger = item.get("trigger");
        return new Agent(
                item.get("tenantId").s(),
                item.get("name").s(),
                Integer.parseInt(item.get("latestVersion").n()),
                active == null ? null : Integer.parseInt(active.n()),
                trigger == null ? null : trigger.s(),
                Instant.parse(item.get("createdAt").s()));
    }

    private static boolean isConditionFailure(TransactionCanceledException e) {
        return e.hasCancellationReasons() && e.cancellationReasons().stream()
                .anyMatch(reason -> "ConditionalCheckFailed".equals(reason.code()));
    }

    private static void backOff(int attempt) {
        try {
            Thread.sleep(ThreadLocalRandom.current().nextLong(5, 10L + 10L * attempt));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while publishing", e);
        }
    }

    static String agentPk(String tenantId, String name) {
        return "TENANT#" + tenantId + "#AGENT#" + name;
    }

    static String versionSk(int version) {
        return "VERSION#%010d".formatted(version);
    }

    private static Map<String, AttributeValue> key(String pk, String sk) {
        return Map.of("PK", s(pk), "SK", s(sk));
    }

    private static AttributeValue s(String value) {
        return AttributeValue.fromS(value);
    }

    private static AttributeValue n(int value) {
        return AttributeValue.fromN(Integer.toString(value));
    }
}
