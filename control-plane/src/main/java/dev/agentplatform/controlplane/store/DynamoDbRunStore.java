package dev.agentplatform.controlplane.store;

import dev.agentplatform.controlplane.approval.ApprovalMessage;
import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.tenant.Principals;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Locale;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ThreadLocalRandom;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.ReturnValue;
import software.amazon.awssdk.services.dynamodb.model.ReturnValuesOnConditionCheckFailure;
import software.amazon.awssdk.services.dynamodb.model.TransactWriteItem;
import software.amazon.awssdk.services.dynamodb.model.TransactionCanceledException;
import software.amazon.awssdk.services.dynamodb.model.TransactionConflictException;

/**
 * {@link RunStore} on the control-plane DynamoDB table (ADR 0008). Item layout:
 *
 * <pre>
 * Run  PK = RUN#&lt;id&gt;  SK = META
 *      runId, tenantId, agentName, agentVersion, status, steps, costUsd, traceId, traceParent,
 *      pendingApproval (JSON), approval (JSON), approvalChannel, approvalMessageId, failureReason,
 *      failureMessage, output, input (JSON), startedBy, createdAt, updatedAt
 *      GSI2PK = TENANT#&lt;t&gt;#AGENT#&lt;name&gt;  GSI2SK = &lt;createdAt&gt;#&lt;runId&gt;  (runs of an agent by time)
 *      setAsideSeconds (total delay of its set-asides), holdsSlot (true while it holds a slot)
 *      GSI1PK = RUNS#QUEUED  GSI1SK = &lt;queued-at epoch millis, 13 digits&gt;#&lt;runId&gt;
 *        (only while QUEUED: a sparse index of queued runs for the re-enqueue sweep; a delayed
 *        run's queued-at is when its message is due)
 *      GSI1PK = RUNS#RUNNING#&lt;0-7, from the run id&gt;  GSI1SK = &lt;last update epoch millis&gt;#&lt;runId&gt;
 *        (only while RUNNING: for the stalled-run sweep)
 *      GSI1PK = RUNS#APPROVAL_UNSENT | RUNS#APPROVAL_SENT
 *      GSI1SK = &lt;waiting-since epoch millis, 13 digits&gt;#&lt;runId&gt;
 *        (only while WAITING_APPROVAL: UNSENT until the call is posted, then SENT; the approval
 *        sweep reads UNSENT, approval expiry reads both)
 * Slots  PK = TENANT#&lt;t&gt;  SK = USAGE            running (N): slots held (ADR 0026)
 * Spend  PK = TENANT#&lt;t&gt;  SK = SPEND#&lt;yyyy-mm-dd&gt; usd (N), ttl (35 days)
 * </pre>
 *
 * GSI1 is shared with agents ({@code TRIGGER#...} keys); the {@code RUNS#...} partitions never
 * collide with them. Queued-at is zero-padded epoch millis so text order is time order.
 *
 * A status change reads the run, then writes on the condition that its status, cost and slot are
 * still what was read (retried with backoff otherwise). A change that takes or frees a slot is one
 * transaction with the tenant's USAGE item; taking one is conditional on {@code running < max}.
 * Spend is a separate {@code ADD} after the change. A decision is one conditional update on status
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
        if (run.traceParent() != null) {
            item.put("traceId", s(run.traceId()));
            item.put("traceParent", s(run.traceParent()));
        }
        item.put("input", s(run.inputJson()));
        item.put("startedBy", s(run.startedBy()));
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
    public Run update(String runId, RunUpdate update, Instant now, int maxRunning) {
        return change(runId, update, now, maxRunning, null).orElseThrow();
    }

    @Override
    public Optional<Run> failStalled(String runId, Instant lastUpdatedAt, Run.Failure failure, Instant now) {
        return change(runId, new RunUpdate(RunStatus.FAILED, null, null, null, null, failure, null), now,
                Integer.MAX_VALUE, lastUpdatedAt);
    }

    /**
     * Reads the run, then writes the change on the condition that what the write depends on (status,
     * cost, slot, and {@code expectUpdatedAt} if given) is unchanged; retried on a concurrent change.
     * With {@code expectUpdatedAt}, a run that is missing, not RUNNING or updated since is empty
     * instead of an exception.
     */
    private Optional<Run> change(String runId, RunUpdate update, Instant now, int maxRunning, Instant expectUpdatedAt) {
        for (int attempt = 1; ; attempt++) {
            var item = read(runId);
            if (item == null) {
                if (expectUpdatedAt != null) {
                    return Optional.empty();
                }
                throw new RunNotFoundException(runId);
            }
            var current = toRun(item);
            if (expectUpdatedAt != null
                    && (current.status() != RunStatus.RUNNING || !current.updatedAt().equals(expectUpdatedAt))) {
                return Optional.empty();
            }
            if (!update.status().allowedFrom().contains(current.status())) {
                throw new IllegalTransitionException(runId, current.status(), update.status());
            }
            var holds = item.containsKey(HOLDS_SLOT);
            var take = current.status() == RunStatus.QUEUED && update.status() == RunStatus.RUNNING;
            var release = holds && update.status() != RunStatus.RUNNING;

            var w = statusWrite(runId, update, now);
            w.expectSeen(current, holds);
            if (expectUpdatedAt != null) {
                w.conditions.add("updatedAt = :seenUpdated");
                w.values.put(":seenUpdated", s(expectUpdatedAt.toString()));
            }
            if (take) {
                w.sets.add(HOLDS_SLOT + " = :holds");
                w.values.put(":holds", AttributeValue.fromBool(true));
            } else if (release) {
                w.removes.add(HOLDS_SLOT);
            }
            var outcome = commit(runId, w, take ? 1 : release ? -1 : 0, current.tenantId(), maxRunning);
            switch (outcome.result()) {
                case DONE -> {
                    var updated = outcome.item() != null ? toRun(outcome.item()) : toRun(read(runId));
                    addSpend(current.tenantId(), updated.costUsd().subtract(current.costUsd()), now);
                    return Optional.of(updated);
                }
                case AT_CAP -> throw new TenantAtCapException(current.tenantId(), maxRunning);
                case CHANGED -> backOff(runId, attempt);
            }
        }
    }

    @Override
    public Run setAside(String runId, int delaySeconds, Instant dueAt, Instant now) {
        for (int attempt = 1; ; attempt++) {
            var item = read(runId);
            if (item == null) {
                throw new RunNotFoundException(runId);
            }
            var current = toRun(item);
            if (current.status() != RunStatus.RUNNING) {
                throw new IllegalTransitionException(runId, current.status(), RunStatus.QUEUED);
            }
            var holds = item.containsKey(HOLDS_SLOT);
            var w = new Write();
            w.sets.addAll(List.of("#status = :to", "updatedAt = :now",
                    "setAsideSeconds = if_not_exists(setAsideSeconds, :zero) + :delay",
                    "GSI1PK = :indexPk", "GSI1SK = :indexSk"));
            w.values.putAll(Map.of(
                    ":to", s(RunStatus.QUEUED.name()),
                    ":now", s(now.toString()),
                    ":zero", n(0),
                    ":delay", n(delaySeconds),
                    ":indexPk", s(QUEUED_PK),
                    ":indexSk", s(indexSk(dueAt, runId))));
            w.expectSeen(current, holds);
            if (holds) {
                w.removes.add(HOLDS_SLOT);
            }
            var outcome = commit(runId, w, holds ? -1 : 0, current.tenantId(), Integer.MAX_VALUE);
            if (outcome.result() == Result.DONE) {
                return outcome.item() != null ? toRun(outcome.item()) : toRun(read(runId));
            }
            backOff(runId, attempt);
        }
    }

    @Override
    public Optional<Run> failQueued(String runId, Run.Failure failure, Instant now) {
        try {
            var result = dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                    .updateExpression("SET #status = :failed, updatedAt = :now, failureReason = :reason,"
                            + " failureMessage = :message REMOVE GSI1PK, GSI1SK")
                    .conditionExpression("#status = :queued")
                    .expressionAttributeNames(Map.of("#status", "status"))
                    .expressionAttributeValues(Map.of(
                            ":failed", s(RunStatus.FAILED.name()),
                            ":now", s(now.toString()),
                            ":reason", s(failure.reason()),
                            ":message", s(failure.message()),
                            ":queued", s(RunStatus.QUEUED.name())))
                    .returnValues(ReturnValue.ALL_NEW));
            return Optional.of(toRun(result.attributes()));
        } catch (ConditionalCheckFailedException e) {
            return Optional.empty(); // missing, or no longer QUEUED
        }
    }

    @Override
    public List<Run> findRunningBefore(Instant cutoff, int limit) {
        var items = new ArrayList<Map<String, AttributeValue>>();
        for (int shard = 0; shard < RUNNING_SHARDS; shard++) {
            var pk = RUNNING_PK + shard;
            items.addAll(dynamo.query(r -> r.tableName(table)
                    .indexName(DynamoDbAgentStore.GSI_TRIGGER)
                    .keyConditionExpression("GSI1PK = :pk AND GSI1SK < :cutoff")
                    .expressionAttributeValues(Map.of(":pk", s(pk), ":cutoff", s(epochMillis(cutoff))))
                    .scanIndexForward(true)
                    .limit(limit)).items());
        }
        return items.stream()
                .sorted(Comparator.comparing((Map<String, AttributeValue> item) -> item.get("GSI1SK").s()))
                .limit(limit)
                .map(DynamoDbRunStore::toRun)
                .toList();
    }

    @Override
    public int running(String tenantId) {
        var item = dynamo.getItem(r -> r.tableName(table).consistentRead(true).key(usageKey(tenantId))).item();
        return item == null || !item.containsKey("running") ? 0 : Integer.parseInt(item.get("running").n());
    }

    @Override
    public BigDecimal spend(String tenantId, LocalDate day) {
        var item = dynamo.getItem(r -> r.tableName(table).consistentRead(true).key(spendKey(tenantId, day))).item();
        return item == null || !item.containsKey("usd") ? BigDecimal.ZERO : new BigDecimal(item.get("usd").n());
    }

    /** SET/REMOVE parts, condition and placeholders of one run write. */
    private static final class Write {
        final List<String> sets = new ArrayList<>();
        final List<String> removes = new ArrayList<>();
        final List<String> conditions = new ArrayList<>();
        final Map<String, AttributeValue> values = new HashMap<>();
        final Map<String, String> names = new HashMap<>(Map.of("#status", "status"));

        /** Only if status, cost and slot are still what was read. */
        void expectSeen(Run seen, boolean holds) {
            conditions.add("#status = :seenStatus");
            conditions.add("costUsd = :seenCost");
            conditions.add(holds ? "attribute_exists(" + HOLDS_SLOT + ")" : "attribute_not_exists(" + HOLDS_SLOT + ")");
            values.put(":seenStatus", s(seen.status().name()));
            values.put(":seenCost", AttributeValue.fromN(seen.costUsd().toPlainString()));
        }

        String expression() {
            return "SET " + String.join(", ", sets) + (removes.isEmpty() ? "" : " REMOVE " + String.join(", ", removes));
        }

        String condition() {
            return String.join(" AND ", conditions);
        }
    }

    /** The status change and progress fields of {@code update}, with the run's index entry for its new status. */
    private static Write statusWrite(String runId, RunUpdate update, Instant now) {
        var w = new Write();
        w.sets.addAll(List.of("#status = :to", "updatedAt = :now"));
        w.values.put(":to", s(update.status().name()));
        w.values.put(":now", s(now.toString()));
        if (update.steps() != null) {
            w.sets.add("steps = :steps");
            w.values.put(":steps", n(update.steps()));
        }
        if (update.costUsd() != null) {
            w.sets.add("costUsd = :cost");
            w.values.put(":cost", AttributeValue.fromN(update.costUsd().toPlainString()));
        }
        if (update.traceId() != null) {
            w.sets.add("traceId = if_not_exists(traceId, :trace)"); // the control plane's trace wins
            w.values.put(":trace", s(update.traceId()));
        }
        if (update.pendingApprovalJson() != null) {
            w.sets.add("pendingApproval = :pending");
            w.values.put(":pending", s(update.pendingApprovalJson()));
        }
        if (update.failure() != null) {
            w.sets.add("failureReason = :reason");
            w.sets.add("failureMessage = :message");
            w.values.put(":reason", s(update.failure().reason()));
            w.values.put(":message", s(update.failure().message()));
        }
        if (update.output() != null) {
            w.sets.add("#output = :output");
            w.values.put(":output", s(update.output()));
            w.names.put("#output", "output");
        }
        switch (update.status()) {
            case QUEUED -> index(w, QUEUED_PK, now, runId);
            case RUNNING -> index(w, runningPk(runId), now, runId);
            case WAITING_APPROVAL -> {
                index(w, UNSENT_PK, now, runId);
                w.removes.addAll(List.of("approvalChannel", "approvalMessageId")); // a new pause is not posted yet
            }
            default -> w.removes.addAll(List.of("GSI1PK", "GSI1SK"));
        }
        return w;
    }

    private static void index(Write w, String pk, Instant at, String runId) {
        w.sets.add("GSI1PK = :indexPk");
        w.sets.add("GSI1SK = :indexSk");
        w.values.put(":indexPk", s(pk));
        w.values.put(":indexSk", s(indexSk(at, runId)));
    }

    private enum Result { DONE, CHANGED, AT_CAP }

    /** {@code item}: the run after a plain update (null after a transaction). */
    private record Outcome(Result result, Map<String, AttributeValue> item) {}

    /**
     * Writes the run alone, or, when {@code slotDelta} is not 0, in one transaction with the
     * tenant's slot counter (taking a slot only below {@code maxRunning}).
     */
    private Outcome commit(String runId, Write w, int slotDelta, String tenantId, int maxRunning) {
        if (slotDelta == 0) {
            try {
                var result = dynamo.updateItem(r -> r.tableName(table).key(key(runId))
                        .updateExpression(w.expression())
                        .conditionExpression(w.condition())
                        .expressionAttributeNames(w.names)
                        .expressionAttributeValues(w.values)
                        .returnValues(ReturnValue.ALL_NEW));
                return new Outcome(Result.DONE, result.attributes());
            } catch (ConditionalCheckFailedException | TransactionConflictException e) {
                return new Outcome(Result.CHANGED, null);
            }
        }
        var runWrite = TransactWriteItem.builder().update(u -> u.tableName(table).key(key(runId))
                .updateExpression(w.expression())
                .conditionExpression(w.condition())
                .expressionAttributeNames(w.names)
                .expressionAttributeValues(w.values)).build();
        var usageValues = new HashMap<String, AttributeValue>(Map.of(":d", n(slotDelta)));
        var slotWrite = TransactWriteItem.builder().update(u -> {
            u.tableName(table).key(usageKey(tenantId)).updateExpression("ADD running :d");
            if (slotDelta > 0) {
                u.conditionExpression("attribute_not_exists(running) OR running < :max");
                usageValues.put(":max", n(maxRunning));
            }
            u.expressionAttributeValues(usageValues);
        }).build();
        try {
            dynamo.transactWriteItems(r -> r.transactItems(runWrite, slotWrite));
            return new Outcome(Result.DONE, null);
        } catch (TransactionCanceledException e) {
            var reasons = e.cancellationReasons();
            var atCap = reasons.size() == 2 && "None".equals(reasons.get(0).code())
                    && "ConditionalCheckFailed".equals(reasons.get(1).code());
            return new Outcome(atCap ? Result.AT_CAP : Result.CHANGED, null);
        }
    }

    /** Waits a little before re-reading after a concurrent change; gives up after {@value #MAX_ATTEMPTS} tries. */
    private static void backOff(String runId, int attempt) {
        if (attempt >= MAX_ATTEMPTS) {
            throw new IllegalStateException("Run '%s' kept changing during %d attempts".formatted(runId, attempt));
        }
        var millis = Math.min(200, 5L << attempt);
        try {
            Thread.sleep(ThreadLocalRandom.current().nextLong(millis / 2, millis + 1));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while updating run " + runId, e);
        }
    }

    /** Adds a run's cost increase to its tenant's spend for the UTC day; a failure is logged, not retried. */
    private void addSpend(String tenantId, BigDecimal increase, Instant now) {
        if (increase.signum() <= 0) {
            return;
        }
        var day = LocalDate.ofInstant(now, ZoneOffset.UTC);
        var expires = day.plusDays(SPEND_KEPT_DAYS).atStartOfDay(ZoneOffset.UTC).toEpochSecond();
        try {
            dynamo.updateItem(r -> r.tableName(table).key(spendKey(tenantId, day))
                    .updateExpression("ADD usd :d SET #ttl = if_not_exists(#ttl, :ttl)")
                    .expressionAttributeNames(Map.of("#ttl", "ttl"))
                    .expressionAttributeValues(Map.of(
                            ":d", AttributeValue.fromN(increase.toPlainString()),
                            ":ttl", AttributeValue.fromN(Long.toString(expires)))));
        } catch (SdkException e) {
            log.warn("Spend of tenant {} not counted ({}): {}", tenantId, increase.toPlainString(), e.getClass().getSimpleName());
        }
    }

    private Map<String, AttributeValue> read(String runId) {
        var item = dynamo.getItem(r -> r.tableName(table).consistentRead(true).key(key(runId))).item();
        return item == null || item.isEmpty() ? null : item;
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

    private static final Logger log = LoggerFactory.getLogger(DynamoDbRunStore.class);
    private static final String QUEUED_PK = "RUNS#QUEUED";
    /** RUNNING runs by last update, over a few partitions so busy runs do not share one. */
    private static final String RUNNING_PK = "RUNS#RUNNING#";
    static final int RUNNING_SHARDS = 8;
    private static final String HOLDS_SLOT = "holdsSlot";
    private static final int MAX_ATTEMPTS = 10;
    private static final int SPEND_KEPT_DAYS = 35;

    static String runningPk(String runId) {
        return RUNNING_PK + Math.floorMod(runId.hashCode(), RUNNING_SHARDS);
    }

    private static Map<String, AttributeValue> usageKey(String tenantId) {
        return Map.of("PK", s(DynamoDbTenantStore.tenantPk(tenantId)), "SK", s("USAGE"));
    }

    private static Map<String, AttributeValue> spendKey(String tenantId, LocalDate day) {
        return Map.of("PK", s(DynamoDbTenantStore.tenantPk(tenantId)), "SK", s("SPEND#" + day));
    }
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
                str(item, "traceParent"),
                str(item, "pendingApproval"),
                str(item, "approval"),
                item.containsKey("approvalMessageId")
                        ? new ApprovalMessage(str(item, "approvalChannel"), str(item, "approvalMessageId"))
                        : null,
                failureReason == null ? null : new Run.Failure(failureReason, str(item, "failureMessage")),
                str(item, "output"),
                item.get("input").s(),
                item.containsKey("startedBy") ? item.get("startedBy").s()
                        : Principals.api(item.get("tenantId").s()), // written before M4
                item.containsKey("setAsideSeconds") ? Integer.parseInt(item.get("setAsideSeconds").n()) : 0,
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
