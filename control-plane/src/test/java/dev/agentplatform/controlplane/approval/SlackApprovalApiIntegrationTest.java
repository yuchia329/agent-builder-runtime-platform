package dev.agentplatform.controlplane.approval;

import static dev.agentplatform.controlplane.approval.FakeSlackServer.fixture;
import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.store.RunStore;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Instant;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.node.ObjectNode;

/** {@code POST /approvals/slack} end to end: signed clicks decide runs; fake Slack sees chat.update. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY,
        "platform.slack.bot-token=xoxb-test-secret-token",
        "platform.slack.approval-channel=C0123456789",
        "platform.slack.signing-secret=" + SlackApprovalApiIntegrationTest.SECRET})
class SlackApprovalApiIntegrationTest {

    static final String SECRET = "test-signing-secret";
    static final FakeSlackServer SLACK = new FakeSlackServer();

    @DynamicPropertySource
    static void slack(DynamicPropertyRegistry registry) {
        registry.add("platform.slack.api-url", SLACK::apiUrl);
    }

    @AfterAll
    static void stop() {
        SLACK.close();
    }

    @Value("${local.server.port}")
    int port;

    @Autowired
    RunStore runs;

    ApiClient api;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
        SLACK.answer(fixture("post_message"));
    }

    private String waitingRun(String agent) throws Exception {
        api.publishExampleAgent(agent);
        var runId = ApiClient.json(api.postJson("/runs", "{\"agent\":\"%s\"}".formatted(agent)))
                .get("run_id").asString();
        api.send("PATCH", "/internal/runs/" + runId, "{\"status\":\"RUNNING\"}", "application/json", false);
        api.send("PATCH", "/internal/runs/" + runId, "{\"status\":\"WAITING_APPROVAL\",\"pending_approval\":"
                + "{\"step\":5,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":7}}}",
                "application/json", false);
        return runId;
    }

    private static byte[] clickBody(String runId, String action, String channel) {
        var payload = SlackInteractionsTest.clickPayload();
        var button = (ObjectNode) payload.get("actions").get(0);
        button.put("action_id", action).put("value", "{\"run_id\":\"%s\",\"step\":5}".formatted(runId));
        ((ObjectNode) payload.get("container")).put("channel_id", channel);
        return SlackInteractionsTest.form(payload.toString());
    }

    private HttpResponse<String> send(byte[] body, String timestamp, String signature) throws Exception {
        var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/approvals/slack"))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .header("X-Slack-Request-Timestamp", timestamp)
                .header("X-Slack-Signature", signature)
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        return HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> signedSend(byte[] body) throws Exception {
        var timestamp = Long.toString(Instant.now().getEpochSecond());
        return send(body, timestamp, SlackInteractions.sign(SECRET, timestamp, body));
    }

    @Test
    void approveClickQueuesTheRunAndUpdatesTheMessage() throws Exception {
        var runId = waitingRun("slack-approve");
        SLACK.answer(fixture("update_message"));
        var before = SLACK.requests().size();

        var response = signedSend(clickBody(runId, "approve", "C0123456789"));

        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(response.body()).isEmpty();
        var run = runs.find(runId).orElseThrow();
        assertThat(run.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(run.approvalJson()).contains("\"decided_by\":\"slack:U0APPROVER1\"");
        var update = SLACK.requests().get(before);
        assertThat(update.path()).isEqualTo("/api/chat.update");
        assertThat(update.body().get("ts").asString()).isEqualTo("1794980400.000100");
        assertThat(update.body().toString()).contains("Approved", "<@U0APPROVER1>");
    }

    @Test
    void signatureCoversTheBytesAsSlackEncodedThem() throws Exception {
        // Slack encodes differently from Java's URLEncoder (%20, unencoded ':'); a body rebuilt from
        // parsed form fields would not match the signature.
        var runId = waitingRun("slack-encoding");
        SLACK.answer(fixture("update_message"));
        var javaEncoded = new String(clickBody(runId, "approve", "C0123456789"), java.nio.charset.StandardCharsets.UTF_8);
        var slackStyle = javaEncoded.replace("+", "%20").replace("%3A", ":").getBytes(java.nio.charset.StandardCharsets.UTF_8);

        assertThat(signedSend(slackStyle).statusCode()).isEqualTo(200);
        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void rejectClickFailsTheRun() throws Exception {
        var runId = waitingRun("slack-reject");
        SLACK.answer(fixture("update_message"));

        assertThat(signedSend(clickBody(runId, "reject", "C0123456789")).statusCode()).isEqualTo(200);

        assertThat(runs.find(runId).orElseThrow().failure().reason()).isEqualTo("approval_rejected");
    }

    @Test
    void failedMessageUpdateStillAnswers200() throws Exception {
        var runId = waitingRun("slack-update-down");
        SLACK.answer(503, "down", java.util.Map.of());

        assertThat(signedSend(clickBody(runId, "approve", "C0123456789")).statusCode()).isEqualTo(200);
        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
    }

    @Test
    void badOrOldSignaturesAreRejected() throws Exception {
        var runId = waitingRun("slack-unsigned");
        var body = clickBody(runId, "approve", "C0123456789");
        var now = Long.toString(Instant.now().getEpochSecond());
        var old = Long.toString(Instant.now().minusSeconds(600).getEpochSecond());

        assertThat(send(body, now, SlackInteractions.sign("wrong", now, body)).statusCode()).isEqualTo(401);
        assertThat(send(body, old, SlackInteractions.sign(SECRET, old, body)).statusCode()).isEqualTo(401);
        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.WAITING_APPROVAL);
    }

    @Test
    void clickFromAnotherChannelIsIgnored() throws Exception {
        var runId = waitingRun("slack-other-channel");

        assertThat(signedSend(clickBody(runId, "approve", "C0OTHER0000")).statusCode()).isEqualTo(200);
        assertThat(runs.find(runId).orElseThrow().status()).isEqualTo(RunStatus.WAITING_APPROVAL);
    }

    @Test
    void malformedSignedBodyIs400() throws Exception {
        assertThat(signedSend("payload=%7Bnot-json".getBytes()).statusCode()).isEqualTo(400);
        assertThat(signedSend("text=hello".getBytes()).statusCode()).isEqualTo(400);
    }

    @Test
    void otherInteractionsGet200() throws Exception {
        var payload = SlackInteractionsTest.clickPayload().put("type", "shortcut");

        assertThat(signedSend(SlackInteractionsTest.form(payload.toString())).statusCode()).isEqualTo(200);
    }
}
