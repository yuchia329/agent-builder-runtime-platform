package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
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
 *      pendingApproval (JSON), approval (JSON), approvalChannel, approvalMessageId, failureReason,
 *      failureMessage, output, input (JSON), createdAt, updatedAt
 *      GSI2PK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  GSI2SK = &lt;createdAt&gt;#&lt;runId&gt;  (runs of an agent by time)
 *      GSI1PK = RUNS#QUEUED  GSI1SK = &lt;queued-at epoch millis, 13 digits&gt;#&lt;runId&gt;
 *        (only while QUEUED: a sparse index of queued runs for the re-enqueue sweep)
 *      GSI1PK = RUNS#APPROVAL_UNSENT | RUNS#APPROVAL_SENT
 *      GSI1SK = &lt;waiting-since epoch millis, 13 digits&gt;#&lt;runId&gt;
 *        (only while WAITING_APPROVAL: UNSENT until the call is posted, then SENT; the approval
 *        sweep reads UNSENT, approval expiry reads both)
 * </pre>
 *
 * GSI1 is shared with agents ({@code TRIGGER#...} keys); the {@code RUNS#...} partitions never
 * collide with them. Queued-at is zero-padded epoch millis so text order is time order.
 *
 * A status change is one conditional update: it applies only if the item exists and its status is
 * one of {@link RunStatus#allowedFrom()}. A decision is one conditional update on status
 * WAITING_APPROVAL and the exact pending call text.
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
            item.put("GSI1SK", s(indexSk(run.createdAt(), run.runId())));
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

        var removes = new ArrayList<String>();
        if (update.status() == RunStatus.QUEUED) {
            sets.add("GSI1PK = :indexPk");
            sets.add("GSI1SK = :indexSk");
            values.put(":indexPk", s(QUEUED_PK));
            values.put(":indexSk", s(indexSk(now, runId)));
        } else if (update.status() == RunStatus.WAITING_APPROVAL) {
            sets.add("GSI1PK = :indexPk");
            sets.add("GSI1SK = :indexSk");
            values.put(":indexPk", s(UNSENT_PK));
            values.put(":indexSk", s(indexSk(now, runId)));
            removes.addAll(List.of("approvalChannel", "approvalMessageId")); // a new pause is not posted yet
        } else {
            removes.addAll(List.of("GSI1PK", "GSI1SK"));
        }
        var expression = "SET " + String.join(", ", sets)
                + (removes.isEmpty() ? "" : " REMOVE " + String.join(", ", removes));

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
                            ":sk", s(indexSk(now, runId)),
                            ":queued", s(RunStatus.QUEUED.name()))));
            return true;
        } catch (ConditionalCheckFailedException e) {
            return false; // missing, or no longer QUEUED
        }
    }

    @Override
    public Run decideApproval(String runId, String pendingApprovalJson, ApprovalDecision decision, Instant now) {
        var sets = new ArrayList<String>(List.of("#status = :to", "updatedAt = :now", "approval = :approval"));
        var values = new HashMap<String, AttributeValue>();
        values.put(":to", s(decision.nextStatus().name()));
        values.put(":now", s(now.toString()));
        values.put(":approval", s(decision.toJson()));
        values.put(":waiting", s(RunStatus.WAITING_APPROVAL.name()));
        values.put(":pending", s(pendingApprovalJson));
        if (decision.approved()) {
            sets.add("GSI1PK = :queuedPk");
            sets.add("GSI1SK = :queuedSk");
            values.put(":queuedPk", s(QUEUED_PK));
            values.put(":queuedSk", s(indexSk(now, runId)));
        } else {
            sets.add("failureReason = :reason");
            sets.add("failureMessage = :message");
            values.put(":reason", s(decision.failure().reason()));
            values.put(":message", s(decision.failure().message()));
        }
        var removes = decision.approved() ? "pendingApproval" : "pendingApproval, GSI1PK, GSI1SK";
        try {
            var result = dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression("SET " + String.join(", ", sets) + " REMOVE " + removes)
                    .conditionExpression("attribute_exists(PK) AND #status = :waiting AND pendingApproval = :pending")
                    .expressionAttributeNames(Map.of("#status", "status"))
                    .expressionAttributeValues(values)
                    .returnValues(ReturnValue.ALL_NEW)
                    .returnValuesOnConditionCheckFailure(ReturnValuesOnConditionCheckFailure.ALL_OLD));
            return toRun(result.attributes());
        } catch (ConditionalCheckFailedException e) {
            if (!e.hasItem() || e.item().isEmpty()) {
                throw new RunNotFoundException(runId);
            }
            var current = RunStatus.valueOf(e.item().get("status").s());
            if (current != RunStatus.WAITING_APPROVAL) {
                throw new IllegalTransitionException(runId, current, decision.nextStatus());
            }
            throw new ApprovalConflictException("Run '%s' is waiting on a different call".formatted(runId));
        }
    }

    @Override
    public List<Run> findUnsentApprovalsBefore(Instant cutoff, int limit) {
        var response = dynamo.query(r -> r.tableName(table)
                .indexName(DynamoDbAgentStore.GSI_TRIGGER)
                .keyConditionExpression("GSI1PK = :pk AND GSI1SK < :cutoff")
                .expressionAttributeValues(Map.of(
                        ":pk", s(UNSENT_PK),
                        ":cutoff", s(epochMillis(cutoff))))
                .scanIndexForward(true)
                .limit(limit));
        return response.items().stream().map(DynamoDbRunStore::toRun).toList();
    }

    @Override
    public List<Run> findWaitingApprovalsBefore(Instant cutoff, int limit) {
        var items = new ArrayList<Map<String, AttributeValue>>();
        for (var pk : List.of(UNSENT_PK, SENT_PK)) {
            items.addAll(dynamo.query(r -> r.tableName(table)
                    .indexName(DynamoDbAgentStore.GSI_TRIGGER)
                    .keyConditionExpression("GSI1PK = :pk AND GSI1SK < :cutoff")
                    .expressionAttributeValues(Map.of(
                            ":pk", s(pk),
                            ":cutoff", s(epochMillis(cutoff))))
                    .scanIndexForward(true)
                    .limit(limit)).items());
        }
        // Each partition is sorted; the oldest `limit` of both are among these.
        return items.stream()
                .sorted(Comparator.comparing((Map<String, AttributeValue> item) -> item.get("GSI1SK").s()))
                .limit(limit)
                .map(DynamoDbRunStore::toRun)
                .toList();
    }

    @Override
    public Optional<Run> expireApproval(String runId, String pendingApprovalJson, Run.Failure failure, Instant now) {
        try {
            var result = dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression("SET #status = :failed, updatedAt = :now, failureReason = :reason,"
                            + " failureMessage = :message REMOVE pendingApproval, GSI1PK, GSI1SK")
                    .conditionExpression("#status = :waiting AND pendingApproval = :pending")
                    .expressionAttributeNames(Map.of("#status", "status"))
                    .expressionAttributeValues(Map.of(
                            ":failed", s(RunStatus.FAILED.name()),
                            ":now", s(now.toString()),
                            ":reason", s(failure.reason()),
                            ":message", s(failure.message()),
                            ":waiting", s(RunStatus.WAITING_APPROVAL.name()),
                            ":pending", s(pendingApprovalJson)))
                    .returnValues(ReturnValue.ALL_NEW));
            return Optional.of(toRun(result.attributes()));
        } catch (ConditionalCheckFailedException e) {
            return Optional.empty(); // missing, decided, or paused on another call
        }
    }

    @Override
    public boolean markApprovalSent(String runId, String pendingApprovalJson, ApprovalMessage message) {
        try {
            dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression("SET approvalChannel = :channel, approvalMessageId = :id, GSI1PK = :sentPk")
                    .conditionExpression("#status = :waiting AND pendingApproval = :pending"
                            + " AND attribute_not_exists(approvalMessageId)")
                    .expressionAttributeNames(Map.of("#status", "status"))
                    .expressionAttributeValues(Map.of(
                            ":channel", s(message.channel()),
                            ":id", s(message.messageId()),
                            ":sentPk", s(SENT_PK),
                            ":waiting", s(RunStatus.WAITING_APPROVAL.name()),
                            ":pending", s(pendingApprovalJson))));
            return true;
        } catch (ConditionalCheckFailedException e) {
            return false; // missing, decided, paused on another call, or already posted
        }
    }

    private static final String QUEUED_PK = "RUNS#QUEUED";
    private static final String UNSENT_PK = "RUNS#APPROVAL_UNSENT";
    private static final String SENT_PK = "RUNS#APPROVAL_SENT";

    /** Index sort key: zero-padded epoch millis (queued-at or waiting-since), then the run id. */
    static String indexSk(Instant at, String runId) {
        return epochMillis(at) + "#" + runId;
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
                str(item, "approval"),
                item.containsKey("approvalMessageId")
                        ? new ApprovalMessage(str(item, "approvalChannel"), str(item, "approvalMessageId"))
                        : null,
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
