package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The {@code chat.postMessage} body for an approval request (Block Kit): a header naming the
 * tool, agent / run / step fields, the arguments, and Approve / Reject buttons. Both buttons carry
 * {@code {"run_id", "step"}} as their value, so a click decides exactly the call it was shown.
 *
 * <p>The arguments come from the model, so they go in a {@code rich_text_preformatted} block:
 * Slack shows that text literally (recorded: backticks, {@code <!channel>} and links stay plain
 * text), unlike mrkdwn, which cannot escape a closing code fence.
 *
 * <p>Once decided, {@code chat.update} replaces the buttons with a context line: "Approved by
 * @person" (a {@code slack:<user id>} decider becomes a mention, others are shown as code) and
 * the time as a Slack date token, which each reader sees in their own time zone.
 */
final class SlackApprovalMessage {

    static final String BLOCK_ID = "approval";
    static final String APPROVE = "approve";
    static final String REJECT = "reject";
    /** Recorded: Slack accepts 12,000 characters here; a person reads far fewer. */
    static final int MAX_ARGUMENTS = 3_000;

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private SlackApprovalMessage() {}

    /** {@code chat.postMessage} body: the request with Approve / Reject buttons. */
    static ObjectNode body(String channel, ApprovalRequest request) {
        var body = JSON.createObjectNode();
        body.put("channel", channel);
        body.put("text", "Approval needed: %s wants to call %s (run %s, step %d)".formatted(
                request.agentName(), request.tool(), request.runId(), request.step()));
        body.put("unfurl_links", false);
        body.put("unfurl_media", false);
        var blocks = body.putArray("blocks");
        describe(blocks, request);

        var value = JSON.createObjectNode().put("run_id", request.runId()).put("step", request.step()).toString();
        var buttons = blocks.addObject().put("type", "actions").put("block_id", BLOCK_ID).putArray("elements");
        button(buttons, APPROVE, "Approve", "primary", value);
        button(buttons, REJECT, "Reject", "danger", value);
        return body;
    }

    /** {@code chat.update} body: the same request, its buttons replaced by {@code outcome}. */
    static ObjectNode decided(ApprovalMessage message, ApprovalRequest request, ApprovalOutcome outcome) {
        var body = JSON.createObjectNode();
        body.put("channel", message.channel());
        body.put("ts", message.messageId());
        body.put("text", "%s: %s (run %s, step %d)".formatted(
                summary(outcome), request.tool() == null ? "approval request" : request.tool(),
                request.runId(), request.step()));
        var blocks = body.putArray("blocks");
        describe(blocks, request);
        blocks.addObject().put("type", "context").putArray("elements").addObject()
                .put("type", "mrkdwn").put("text", outcomeText(outcome));
        return body;
    }

    private static void describe(ArrayNode blocks, ApprovalRequest request) {
        var header = blocks.addObject().put("type", "header");
        plainText(header.putObject("text"),
                request.tool() == null ? "Approval request" : "Approval needed: " + request.tool());

        var fields = blocks.addObject().put("type", "section").putArray("fields");
        field(fields, "Agent", request.agentName());
        field(fields, "Run", "`" + request.runId() + "`");
        field(fields, "Step", Integer.toString(request.step()));

        if (request.arguments() != null) {
            var preformatted = blocks.addObject().put("type", "rich_text").putArray("elements").addObject()
                    .put("type", "rich_text_preformatted");
            preformatted.putArray("elements").addObject().put("type", "text").put("text", arguments(request));
        }
    }

    private static String summary(ApprovalOutcome outcome) {
        if (!outcome.isDecision()) {
            return "No longer waiting";
        }
        return (outcome.decision() == Decision.approved ? "Approved" : "Rejected") + " by " + outcome.decidedBy();
    }

    static String outcomeText(ApprovalOutcome outcome) {
        if (!outcome.isDecision()) {
            return "_" + outcome.note() + "_";
        }
        var verb = outcome.decision() == Decision.approved ? "*Approved*" : "*Rejected*";
        var at = outcome.decidedAt();
        var when = "<!date^%d^{date_short_pretty} at {time}|%s>".formatted(at.getEpochSecond(), at);
        return verb + " by " + who(outcome.decidedBy()) + " " + when;
    }

    /** {@code slack:<user id>} becomes a mention; other deciders (the API) are shown as code. */
    static String who(String decidedBy) {
        if (decidedBy.startsWith("slack:") && decidedBy.substring(6).matches("[A-Z0-9]+")) {
            return "<@" + decidedBy.substring(6) + ">";
        }
        return "`" + decidedBy.replace("`", "'") + "`";
    }

    /** The arguments as indented JSON, cut to {@link #MAX_ARGUMENTS} characters with a note. */
    static String arguments(ApprovalRequest request) {
        var text = JSON.writerWithDefaultPrettyPrinter().writeValueAsString(request.arguments());
        if (text.length() <= MAX_ARGUMENTS) {
            return text;
        }
        return text.substring(0, MAX_ARGUMENTS) + "\n… cut at %,d of %,d characters; see GET /runs/%s"
                .formatted(MAX_ARGUMENTS, text.length(), request.runId());
    }

    private static void field(ArrayNode fields, String label, String value) {
        // Agent names, run ids and tool names are plain identifiers (spec and contract patterns).
        fields.addObject().put("type", "mrkdwn").put("text", "*" + label + "*\n" + value);
    }

    private static void button(ArrayNode buttons, String actionId, String label, String style, String value) {
        var button = buttons.addObject().put("type", "button").put("action_id", actionId).put("style", style)
                .put("value", value);
        plainText(button.putObject("text"), label);
    }

    private static void plainText(ObjectNode node, String text) {
        node.put("type", "plain_text").put("text", text);
    }
}
