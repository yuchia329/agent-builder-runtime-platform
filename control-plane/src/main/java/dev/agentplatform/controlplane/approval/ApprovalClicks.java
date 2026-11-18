package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.ApprovalDecision;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.store.ApprovalConflictException;
import dev.agentplatform.controlplane.store.IllegalTransitionException;
import dev.agentplatform.controlplane.store.RunStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Applies an Approve / Reject click: the same decision path as {@code POST /runs/{id}/approval}
 * ({@link RunService#decideApproval}, decided once, {@code decided_by = slack:<user id>}), then
 * shows the run's outcome on the clicked message. A click on a decided or moved-on call changes
 * nothing and only refreshes the message. Never throws for expected cases: Slack gets its 200.
 *
 * <p>Anyone who can click in the approval channel may decide (channel membership is the
 * permission until M4 maps identities).
 */
@Component
public class ApprovalClicks {

    private static final Logger log = LoggerFactory.getLogger(ApprovalClicks.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final RunService service;
    private final RunStore runs;
    private final ApprovalChannel channel;

    public ApprovalClicks(RunService service, RunStore runs, ApprovalChannel channel) {
        this.service = service;
        this.runs = runs;
        this.channel = channel;
    }

    public void apply(SlackInteractions.Click click) {
        var found = runs.find(click.runId());
        if (found.isEmpty()) {
            log.warn("Approval click by {} for unknown run {}", click.userId(), click.runId());
            return;
        }
        var run = found.get();
        try {
            run = service.decideApproval(run.tenantId(), run.runId(), click.step(), click.decision(),
                    "slack:" + click.userId());
            log.info("Run {} step {} {} by slack:{}", run.runId(), click.step(), click.decision(), click.userId());
        } catch (IllegalTransitionException | ApprovalConflictException e) {
            log.info("Approval click by {} on run {} step {} changed nothing: {}", click.userId(), click.runId(),
                    click.step(), e.getMessage());
            run = runs.find(click.runId()).orElse(run);
        }
        try {
            channel.showOutcome(click.message(), request(run, click.step()), outcome(run, click.step()));
        } catch (ApprovalChannelException e) {
            log.warn("Outcome of run {} step {} not shown in Slack: {}", run.runId(), click.step(), e.getMessage());
        }
    }

    /** The clicked call, from the decision or the pending call of that step; tool unknown otherwise. */
    static ApprovalRequest request(Run run, int step) {
        var call = callAt(run.approvalJson(), step);
        if (call == null) {
            call = callAt(run.pendingApprovalJson(), step);
        }
        return new ApprovalRequest(run.runId(), run.tenantId(), run.agentName(), step,
                call == null ? null : call.get("tool").asString(), call == null ? null : call.get("arguments"));
    }

    static ApprovalOutcome outcome(Run run, int step) {
        var decision = callAt(run.approvalJson(), step);
        if (decision != null) {
            return ApprovalOutcome.decided(ApprovalDecision.Decision.valueOf(decision.get("decision").asString()),
                    decision.get("decided_by").asString(), java.time.Instant.parse(decision.get("decided_at").asString()));
        }
        if (callAt(run.pendingApprovalJson(), step) != null) {
            return ApprovalOutcome.noLongerApplies("Still waiting; the decision was not recorded, try again");
        }
        return ApprovalOutcome.noLongerApplies("No longer waiting for this call (run is %s)".formatted(run.status()));
    }

    private static JsonNode callAt(String json, int step) {
        if (json == null) {
            return null;
        }
        var call = JSON.readTree(json);
        return call.path("step").asInt(0) == step ? call : null;
    }
}
