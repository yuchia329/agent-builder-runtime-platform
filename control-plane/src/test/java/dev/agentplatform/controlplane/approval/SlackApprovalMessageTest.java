package dev.agentplatform.controlplane.approval;

import static dev.agentplatform.controlplane.approval.ApprovalChannelContractTest.REQUEST;
import static dev.agentplatform.controlplane.approval.FakeSlackServer.JSON;
import static dev.agentplatform.controlplane.approval.FakeSlackServer.fixture;
import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

class SlackApprovalMessageTest {

    private final JsonNode body = SlackApprovalMessage.body("C0123456789", REQUEST);

    @Test
    void blocksHaveTheRecordedLayout() {
        // Recorded message: Slack accepted these block types in this order.
        var recorded = JSON.readTree(fixture("post_message")).get("message").get("blocks");
        assertThat(body.get("blocks").valueStream().map(b -> b.get("type").asString()).toList())
                .isEqualTo(recorded.valueStream().map(b -> b.get("type").asString()).toList());
        assertThat(body.get("channel").asString()).isEqualTo("C0123456789");
        assertThat(body.get("text").asString())
                .isEqualTo("Approval needed: issue-triage wants to call github.close_issue (run %s, step 5)"
                        .formatted(REQUEST.runId()));
        assertThat(body.get("blocks").get(0).get("text").get("text").asString())
                .isEqualTo("Approval needed: github.close_issue");
    }

    @Test
    void buttonsCarryRunAndStep() {
        var actions = body.get("blocks").get(3);
        assertThat(actions.get("block_id").asString()).isEqualTo("approval");
        var buttons = actions.get("elements");
        assertThat(buttons.valueStream().map(b -> b.get("action_id").asString()).toList())
                .containsExactly("approve", "reject");
        for (var button : buttons) {
            var value = JSON.readTree(button.get("value").asString());
            assertThat(value.get("run_id").asString()).isEqualTo(REQUEST.runId());
            assertThat(value.get("step").asInt()).isEqualTo(5);
        }
    }

    @Test
    void argumentsAreShownLiterally() {
        var hostile = new ApprovalRequest(REQUEST.runId(), "t1", "issue-triage", 5, "github.close_issue",
                JSON.readTree("{\"note\":\"``` <!channel> *bold* <https://x.example|click>\"}"));

        var text = argumentsText(SlackApprovalMessage.body("C1", hostile));

        // Preformatted rich text is not parsed as mrkdwn (recorded), so nothing needs escaping.
        assertThat(text).contains("``` <!channel> *bold* <https://x.example|click>");
        assertThat(JSON.readTree(text)).isEqualTo(hostile.arguments());
    }

    @Test
    void longArgumentsAreCutWithANote() {
        var huge = JSON.createObjectNode().put("body", "x".repeat(10_000));
        var request = new ApprovalRequest(REQUEST.runId(), "t1", "issue-triage", 5, "github.close_issue", huge);

        var text = argumentsText(SlackApprovalMessage.body("C1", request));

        var full = JSON.writerWithDefaultPrettyPrinter().writeValueAsString(huge);
        assertThat(text).isEqualTo(full.substring(0, 3_000) + "\n… cut at 3,000 of %,d characters; see GET /runs/%s"
                .formatted(full.length(), REQUEST.runId()));
    }

    @Test
    void decidedMessageReplacesButtonsWithWhoAndWhen() {
        var message = new ApprovalMessage("C0123456789", "1794980400.000100");
        var outcome = ApprovalOutcome.decided(Decision.approved, "slack:U0APPROVER1",
                java.time.Instant.parse("2026-11-17T21:52:00Z"));

        var decided = SlackApprovalMessage.decided(message, REQUEST, outcome);

        assertThat(decided.get("channel").asString()).isEqualTo("C0123456789");
        assertThat(decided.get("ts").asString()).isEqualTo("1794980400.000100");
        assertThat(decided.get("blocks").valueStream().map(b -> b.get("type").asString()).toList())
                .containsExactly("header", "section", "rich_text", "context");
        assertThat(decided.get("blocks").get(3).get("elements").get(0).get("text").asString())
                .isEqualTo("*Approved* by <@U0APPROVER1> <!date^1794952320^{date_short_pretty} at {time}|2026-11-17T21:52:00Z>");
        assertThat(decided.get("text").asString()).startsWith("Approved by slack:U0APPROVER1: github.close_issue");
    }

    @Test
    void deciderIsAMentionOnlyForSlackUserIds() {
        assertThat(SlackApprovalMessage.who("slack:U0APPROVER1")).isEqualTo("<@U0APPROVER1>");
        assertThat(SlackApprovalMessage.who("api:tenant-demo")).isEqualTo("`api:tenant-demo`");
        assertThat(SlackApprovalMessage.who("slack:<!channel>")).isEqualTo("`slack:<!channel>`");
    }

    @Test
    void staleRequestWithoutKnownCallShowsOnlyTheNote() {
        var stale = new ApprovalRequest(REQUEST.runId(), "t1", "issue-triage", 3, null, null);

        var decided = SlackApprovalMessage.decided(new ApprovalMessage("C1", "1.2"), stale,
                ApprovalOutcome.noLongerApplies("No longer waiting for this call (run is RUNNING)"));

        assertThat(decided.get("blocks").valueStream().map(b -> b.get("type").asString()).toList())
                .containsExactly("header", "section", "context");
        assertThat(decided.get("blocks").get(0).get("text").get("text").asString()).isEqualTo("Approval request");
        assertThat(decided.get("blocks").get(2).get("elements").get(0).get("text").asString())
                .isEqualTo("_No longer waiting for this call (run is RUNNING)_");
    }

    private static String argumentsText(JsonNode body) {
        return body.get("blocks").get(2).get("elements").get(0).get("elements").get(0).get("text").asString();
    }
}
