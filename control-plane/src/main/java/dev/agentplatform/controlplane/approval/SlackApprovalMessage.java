package dev.agentplatform.controlplane.approval;

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
 */
final class SlackApprovalMessage {

    static final String BLOCK_ID = "approval";
    static final String APPROVE = "approve";
    static final String REJECT = "reject";
    /** Recorded: Slack accepts 12,000 characters here; a person reads far fewer. */
    static final int MAX_ARGUMENTS = 3_000;

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private SlackApprovalMessage() {}

    static ObjectNode body(String channel, ApprovalRequest request) {
        var body = JSON.createObjectNode();
        body.put("channel", channel);
        body.put("text", "Approval needed: %s wants to call %s (run %s, step %d)".formatted(
                request.agentName(), request.tool(), request.runId(), request.step()));
        body.put("unfurl_links", false);
        body.put("unfurl_media", false);
        var blocks = body.putArray("blocks");

        var header = blocks.addObject().put("type", "header");
        plainText(header.putObject("text"), "Approval needed: " + request.tool());

        var fields = blocks.addObject().put("type", "section").putArray("fields");
        field(fields, "Agent", request.agentName());
        field(fields, "Run", "`" + request.runId() + "`");
        field(fields, "Step", Integer.toString(request.step()));

        var preformatted = blocks.addObject().put("type", "rich_text").putArray("elements").addObject()
                .put("type", "rich_text_preformatted");
        preformatted.putArray("elements").addObject().put("type", "text").put("text", arguments(request));

        var value = JSON.createObjectNode().put("run_id", request.runId()).put("step", request.step()).toString();
        var buttons = blocks.addObject().put("type", "actions").put("block_id", BLOCK_ID).putArray("elements");
        button(buttons, APPROVE, "Approve", "primary", value);
        button(buttons, REJECT, "Reject", "danger", value);
        return body;
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
