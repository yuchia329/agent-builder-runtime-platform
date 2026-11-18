package dev.agentplatform.controlplane.approval;

import static dev.agentplatform.controlplane.approval.FakeSlackServer.JSON;
import static dev.agentplatform.controlplane.approval.FakeSlackServer.fixture;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.run.ApprovalDecision.Decision;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.node.ObjectNode;

class SlackInteractionsTest {

    // Slack docs, "Verifying requests from Slack": the worked example, byte for byte.
    private static final String DOC_SECRET = "8f742231b10e8888abcd99yyyzzz85a5";
    private static final String DOC_TIMESTAMP = "1531420618";
    private static final byte[] DOC_BODY = ("token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow"
            + "&channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner"
            + "&command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands%2FT1DC2JH3J"
            + "%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c")
            .getBytes(StandardCharsets.UTF_8);
    private static final String DOC_SIGNATURE = "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503";
    private static final Instant DOC_NOW = Instant.ofEpochSecond(1531420618 + 10);

    @Test
    void documentedExampleVerifies() {
        assertThat(SlackInteractions.sign(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY)).isEqualTo(DOC_SIGNATURE);
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY, DOC_SIGNATURE, DOC_NOW))
                .isTrue();
    }

    @Test
    void wrongSecretOrChangedBodyFails() {
        assertThat(SlackInteractions.signatureMatches("other", DOC_TIMESTAMP, DOC_BODY, DOC_SIGNATURE, DOC_NOW)).isFalse();
        var changed = DOC_BODY.clone();
        changed[changed.length - 1] = '0';
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, changed, DOC_SIGNATURE, DOC_NOW)).isFalse();
    }

    @ParameterizedTest
    @ValueSource(longs = {-301, 301, 3600})
    void timestampMoreThanFiveMinutesAwayFails(long offset) {
        var now = Instant.ofEpochSecond(1531420618 + offset);

        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY, DOC_SIGNATURE, now)).isFalse();
    }

    @Test
    void fiveMinutesExactlyIsStillAccepted() {
        var now = Instant.ofEpochSecond(1531420618 + 300);

        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY, DOC_SIGNATURE, now)).isTrue();
    }

    @Test
    void missingOrMalformedHeadersFail() {
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, null, DOC_BODY, DOC_SIGNATURE, DOC_NOW)).isFalse();
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, "soon", DOC_BODY, DOC_SIGNATURE, DOC_NOW)).isFalse();
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY, null, DOC_NOW)).isFalse();
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY,
                DOC_SIGNATURE.replace("v0=", "v1="), DOC_NOW)).isFalse();
        assertThat(SlackInteractions.signatureMatches(DOC_SECRET, DOC_TIMESTAMP, DOC_BODY, "v0=zz", DOC_NOW)).isFalse();
    }

    // Clicks

    static byte[] form(String payloadJson) {
        return ("payload=" + URLEncoder.encode(payloadJson, StandardCharsets.UTF_8)).getBytes(StandardCharsets.UTF_8);
    }

    static ObjectNode clickPayload() {
        return (ObjectNode) JSON.readTree(fixture("block_actions"));
    }

    @Test
    void approveClickIsRead() {
        var click = SlackInteractions.click(form(clickPayload().toString())).orElseThrow();

        assertThat(click).isEqualTo(new SlackInteractions.Click("run_" + "0".repeat(32), 5, Decision.approved,
                "U0APPROVER1", new ApprovalMessage("C0123456789", "1794980400.000100")));
    }

    @Test
    void rejectClickIsRead() {
        var payload = clickPayload();
        ((ObjectNode) payload.get("actions").get(0)).put("action_id", "reject");

        assertThat(SlackInteractions.click(form(payload.toString())).orElseThrow().decision()).isEqualTo(Decision.rejected);
    }

    @Test
    void otherInteractionsAreIgnored() {
        var otherType = clickPayload().put("type", "view_submission");
        var otherBlock = clickPayload();
        ((ObjectNode) otherBlock.get("actions").get(0)).put("block_id", "something-else");
        var otherAction = clickPayload();
        ((ObjectNode) otherAction.get("actions").get(0)).put("action_id", "maybe");

        for (var payload : java.util.List.of(otherType, otherBlock, otherAction)) {
            assertThat(SlackInteractions.click(form(payload.toString()))).isEmpty();
        }
    }

    @Test
    void malformedClicksAreRefused() {
        assertThatThrownBy(() -> SlackInteractions.click("text=hello".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("no payload");
        var badValue = clickPayload();
        ((ObjectNode) badValue.get("actions").get(0)).put("value", "not json");
        assertThatThrownBy(() -> SlackInteractions.click(form(badValue.toString())))
                .isInstanceOf(IllegalArgumentException.class);
        var noUser = clickPayload();
        noUser.remove("user");
        assertThatThrownBy(() -> SlackInteractions.click(form(noUser.toString())))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("missing");
    }
}
