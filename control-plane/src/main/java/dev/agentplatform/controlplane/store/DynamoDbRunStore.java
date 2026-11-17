package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Locale;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.ReturnValue;
import software.amazon.awssdk.services.dynamodb.model.ReturnValuesOnConditionCheckFailure;

/**
 * {@link RunStore} on the control-plane DynamoDB table (ADR 0008). Item layout:
 *
 * <pre>
 * Run  PK = RUN#&lt;id&gt;  SK = META
 *      runId, tenantId, agentName, agentVersion, status, steps, costUsd, traceId,
 *      pendingApproval (JSON), failureReason, failureMessage, output, input (JSON),
 *      createdAt, updatedAt
 *      GSI2PK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  GSI2SK = &lt;createdAt&gt;#&lt;runId&gt;  (runs of an agent by time)
 *      GSI1PK = RUNS#QUEUED  GSI1SK = &lt;queued-at epoch millis, 13 digits&gt;#&lt;runId&gt;
 *        (only while QUEUED: a sparse index of queued runs for the re-enqueue sweep)
 * </pre>
 *
 * GSI1 is shared with agents ({@code TRIGGER#...} keys); the {@code RUNS#QUEUED} partition never
 * collides with them. Queued-at is zero-padded epoch millis so text order is time order.
 *
 * A status change is one conditional update: it applies only if the item exists and its status is
 * one of {@link RunStatus#allowedFrom()}.
 */
public class DynamoDbRunStore implements RunStore {

    private final DynamoDbClient dynamo;
    private final String table;

    public DynamoDbRunStore(DynamoDbClient dynamo, String table) {
        this.dynamo = dynamo;
        this.table = table;
    }

    @Override
    public void create(Run run) {
        var item = new HashMap<String, AttributeValue>();
        item.put("PK", s(runPk(run.runId())));
        item.put("SK", s("META"));
        item.put("runId", s(run.runId()));
        item.put("tenantId", s(run.tenantId()));
        item.put("agentName", s(run.agentName()));
        item.put("agentVersion", n(run.agentVersion()));
        item.put("status", s(run.status().name()));
        item.put("steps", n(run.steps()));
        item.put("costUsd", AttributeValue.fromN(run.costUsd().toPlainString()));
        item.put("input", s(run.inputJson()));
        item.put("createdAt", s(run.createdAt().toString()));
        item.put("updatedAt", s(run.updatedAt().toString()));
        item.put("GSI2PK", s(DynamoDbAgentStore.agentPk(run.tenantId(), run.agentName())));
        item.put("GSI2SK", s(run.createdAt() + "#" + run.runId()));
        if (run.status() == RunStatus.QUEUED) {
            item.put("GSI1PK", s(QUEUED_PK));
            item.put("GSI1SK", s(queuedSk(run.createdAt(), run.runId())));
        }
        try {
            dynamo.putItem(r -> r.tableName(table).item(item).conditionExpression("attribute_not_exists(PK)"));
        } catch (ConditionalCheckFailedException e) {
            throw new IllegalStateException("Run '%s' already exists".formatted(run.runId()), e);
        }
    }

    @Override
    public Optional<Run> find(String runId) {
        var item = dynamo.getItem(r -> r.tableName(table).consistentRead(true).key(key(runId))).item();
        return item == null || item.isEmpty() ? Optional.empty() : Optional.of(toRun(item));
    }

    @Override
    public Run update(String runId, RunUpdate update, Instant now) {
        var sets = new ArrayList<String>(List.of("#status = :to", "updatedAt = :now"));
        var values = new HashMap<String, AttributeValue>();
        values.put(":to", s(update.status().name()));
        values.put(":now", s(now.toString()));
        if (update.steps() != null) {
            sets.add("steps = :steps");
            values.put(":steps", n(update.steps()));
        }
        if (update.costUsd() != null) {
            sets.add("costUsd = :cost");
            values.put(":cost", AttributeValue.fromN(update.costUsd().toPlainString()));
        }
        if (update.traceId() != null) {
            sets.add("traceId = :trace");
            values.put(":trace", s(update.traceId()));
        }
        if (update.pendingApprovalJson() != null) {
            sets.add("pendingApproval = :pending");
            values.put(":pending", s(update.pendingApprovalJson()));
        }
        if (update.failure() != null) {
            sets.add("failureReason = :reason");
            sets.add("failureMessage = :message");
            values.put(":reason", s(update.failure().reason()));
            values.put(":message", s(update.failure().message()));
        }
        if (update.output() != null) {
            sets.add("#output = :output");
            values.put(":output", s(update.output()));
        }

        var queued = update.status() == RunStatus.QUEUED;
        if (queued) {
            sets.add("GSI1PK = :queuedPk");
            sets.add("GSI1SK = :queuedSk");
            values.put(":queuedPk", s(QUEUED_PK));
            values.put(":queuedSk", s(queuedSk(now, runId)));
        }
        var expression = "SET " + String.join(", ", sets) + (queued ? "" : " REMOVE GSI1PK, GSI1SK");

        var allowed = new ArrayList<String>();
        for (var from : update.status().allowedFrom()) {
            var placeholder = ":from" + allowed.size();
            allowed.add(placeholder);
            values.put(placeholder, s(from.name()));
        }
        var condition = allowed.isEmpty()
                ? "attribute_exists(PK) AND attribute_not_exists(PK)" // never true: no API path to this status
                : "attribute_exists(PK) AND #status IN (" + String.join(", ", allowed) + ")";
        var names = new HashMap<String, String>(Map.of("#status", "status"));
        if (update.output() != null) {
            names.put("#output", "output");
        }

        try {
            var result = dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression(expression)
                    .conditionExpression(condition)
                    .expressionAttributeNames(names)
                    .expressionAttributeValues(values)
                    .returnValues(ReturnValue.ALL_NEW)
                    .returnValuesOnConditionCheckFailure(ReturnValuesOnConditionCheckFailure.ALL_OLD));
            return toRun(result.attributes());
        } catch (ConditionalCheckFailedException e) {
            if (!e.hasItem() || e.item().isEmpty()) {
                throw new RunNotFoundException(runId);
            }
            throw new IllegalTransitionException(runId, RunStatus.valueOf(e.item().get("status").s()), update.status());
        }
    }

    @Override
    public List<Run> findQueuedBefore(Instant cutoff, int limit) {
        var response = dynamo.query(r -> r.tableName(table)
                .indexName(DynamoDbAgentStore.GSI_TRIGGER)
                .keyConditionExpression("GSI1PK = :pk AND GSI1SK < :cutoff")
                .expressionAttributeValues(Map.of(
                        ":pk", s(QUEUED_PK),
                        ":cutoff", s(epochMillis(cutoff))))
                .scanIndexForward(true)
                .limit(limit));
        return response.items().stream().map(DynamoDbRunStore::toRun).toList();
    }

    @Override
    public boolean touchQueued(String runId, Instant now) {
        try {
            dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression("SET GSI1SK = :sk")
                    .conditionExpression("#status = :queued")
                    .expressionAttributeNames(Map.of("#status", "status"))
                    .expressionAttributeValues(Map.of(
                            ":sk", s(queuedSk(now, runId)),
                            ":queued", s(RunStatus.QUEUED.name()))));
            return true;
        } catch (ConditionalCheckFailedException e) {
            return false; // missing, or no longer QUEUED
        }
    }

    private static final String QUEUED_PK = "RUNS#QUEUED";

    static String queuedSk(Instant queuedAt, String runId) {
        return epochMillis(queuedAt) + "#" + runId;
    }

    private static String epochMillis(Instant instant) {
        return String.format(Locale.ROOT, "%013d", instant.toEpochMilli());
    }

    private static Run toRun(Map<String, AttributeValue> item) {
        var failureReason = str(item, "failureReason");
        return new Run(
                item.get("runId").s(),
                item.get("tenantId").s(),
                item.get("agentName").s(),
                Integer.parseInt(item.get("agentVersion").n()),
                RunStatus.valueOf(item.get("status").s()),
                Integer.parseInt(item.get("steps").n()),
                new BigDecimal(item.get("costUsd").n()),
                str(item, "traceId"),
                str(item, "pendingApproval"),
                failureReason == null ? null : new Run.Failure(failureReason, str(item, "failureMessage")),
                str(item, "output"),
                item.get("input").s(),
                Instant.parse(item.get("createdAt").s()),
                Instant.parse(item.get("updatedAt").s()));
    }

    private static String str(Map<String, AttributeValue> item, String name) {
        var value = item.get(name);
        return value == null ? null : value.s();
    }

    static String runPk(String runId) {
        return "RUN#" + runId;
    }

    private static Map<String, AttributeValue> key(String runId) {
        return Map.of("PK", s(runPk(runId)), "SK", s("META"));
    }

    private static AttributeValue s(String value) {
        return AttributeValue.fromS(value);
    }

    private static AttributeValue n(int value) {
        return AttributeValue.fromN(Integer.toString(value));
    }
}
