package dev.agentplatform.controlplane.approval;

import static dev.agentplatform.controlplane.approval.FakeSlackServer.fixture;
import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.store.RunStore;
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

/** A worker's WAITING_APPROVAL report posts to (fake) Slack; a failed post does not fail the report. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY,
        "platform.slack.bot-token=xoxb-test-secret-token",
        "platform.slack.approval-channel=C0123456789"})
class ApprovalPostIntegrationTest {

    static final FakeSlackServer SLACK = new FakeSlackServer();

    @DynamicPropertySource
    static void slack(DynamicPropertyRegistry registry) {
        registry.add("platform.slack.api-url", SLACK::apiUrl);
    }

    @AfterAll
    static void stop() {
        SLACK.close();
    }

    private final ContractValidator contracts = new ContractValidator();

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

    private HttpResponse<String> patch(String runId, String body) throws Exception {
        return api.send("PATCH", "/internal/runs/" + runId, body, "application/json", false);
    }

    private String runningRun(String agent) throws Exception {
        api.publishExampleAgent(agent);
        var runId = ApiClient.json(api.postJson("/runs", "{\"agent\":\"%s\"}".formatted(agent)))
                .get("run_id").asString();
        patch(runId, "{\"status\":\"RUNNING\"}");
        return runId;
    }

    private HttpResponse<String> pause(String runId) throws Exception {
        return patch(runId, "{\"status\":\"WAITING_APPROVAL\",\"steps\":6,\"pending_approval\":"
                + "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}}");
    }

    @Test
    void pauseIsPostedToSlack() throws Exception {
        var runId = runningRun("post-slack");
        var before = SLACK.requests().size();

        var response = pause(runId);

        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, ApiClient.json(response))).isEmpty();
        var posts = SLACK.requests().subList(before, SLACK.requests().size());
        assertThat(posts).hasSize(1);
        var body = posts.getFirst().body();
        assertThat(body.get("channel").asString()).isEqualTo("C0123456789");
        assertThat(body.get("text").asString()).contains("post-slack", "github.close_issue", runId, "step 7");
        assertThat(runs.find(runId).orElseThrow().approvalMessage())
                .isEqualTo(new ApprovalMessage("C0123456789", "1794980400.000100"));
    }

    @Test
    void failedPostDoesNotFailTheReport() throws Exception {
        var runId = runningRun("post-slack-down");
        SLACK.answer(fixture("post_message_channel_not_found"));

        var response = pause(runId);

        assertThat(response.statusCode()).isEqualTo(200);
        var run = runs.find(runId).orElseThrow();
        assertThat(run.status()).isEqualTo(RunStatus.WAITING_APPROVAL);
        assertThat(run.approvalMessage()).isNull();
        assertThat(runs.findUnsentApprovalsBefore(Instant.now().plusSeconds(1), 100))
                .extracting(r -> r.runId()).contains(runId); // left for the approval sweep
    }
}
