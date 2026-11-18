package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** {@code POST /runs/{id}/approval} with in-memory store and queue; responses checked against the contract. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class ApprovalApiIntegrationTest {

    private final ContractValidator contracts = new ContractValidator();

    @Value("${local.server.port}")
    int port;

    @Autowired
    RunQueuePublisher publisher;

    ApiClient api;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
    }

    private HttpResponse<String> patch(String runId, String body) throws Exception {
        return api.send("PATCH", "/internal/runs/" + runId, body, "application/json", false);
    }

    /** A run paused at step 7 on github.close_issue. */
    private String waitingRun(String agent) throws Exception {
        api.publishExampleAgent(agent);
        var runId = ApiClient.json(api.postJson("/runs", "{\"agent\":\"%s\"}".formatted(agent)))
                .get("run_id").asString();
        patch(runId, "{\"status\":\"RUNNING\"}");
        var waiting = patch(runId, "{\"status\":\"WAITING_APPROVAL\",\"steps\":6,\"pending_approval\":"
                + "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}}");
        assertThat(waiting.statusCode()).isEqualTo(200);
        return runId;
    }

    private HttpResponse<String> decide(String runId, String decision, int step) throws Exception {
        return api.postJson("/runs/" + runId + "/approval",
                "{\"decision\":\"%s\",\"step\":%d}".formatted(decision, step));
    }

    private int queued() {
        return ((InMemoryRunQueuePublisher) publisher).bodies().size();
    }

    @Test
    void approvalRequeuesTheRunAndWorkersSeeTheDecision() throws Exception {
        var runId = waitingRun("approve-run");
        var before = queued();

        var response = decide(runId, "approved", 7);

        assertThat(response.statusCode()).isEqualTo(200);
        var run = ApiClient.json(response);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, run)).isEmpty();
        assertThat(run.get("status").asString()).isEqualTo("QUEUED");
        assertThat(run.has("pending_approval")).isFalse();
        var approval = run.get("approval");
        assertThat(approval.get("decision").asString()).isEqualTo("approved");
        assertThat(approval.get("step").asInt()).isEqualTo(7);
        assertThat(approval.get("tool").asString()).isEqualTo("github.close_issue");
        assertThat(approval.get("arguments").get("issue_number").asInt()).isEqualTo(42);
        assertThat(approval.get("decided_by").asString()).isEqualTo("api:tenant-test");

        var bodies = ((InMemoryRunQueuePublisher) publisher).bodies();
        assertThat(bodies).hasSize(before + 1);
        assertThat(bodies.getLast()).contains("\"run_id\":\"" + runId + "\"");

        var internal = ApiClient.json(api.send("GET", "/internal/runs/" + runId, null, null, false));
        assertThat(contracts.validate(ContractValidator.INTERNAL_RUN, internal)).isEmpty();
        assertThat(internal.get("approval")).isEqualTo(approval);

        // The resumed worker can pick the run up again.
        assertThat(patch(runId, "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(200);
    }

    @Test
    void rejectionFailsTheRun() throws Exception {
        var runId = waitingRun("reject-run");
        var before = queued();

        var response = decide(runId, "rejected", 7);

        assertThat(response.statusCode()).isEqualTo(200);
        var run = ApiClient.json(response);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, run)).isEmpty();
        assertThat(run.get("status").asString()).isEqualTo("FAILED");
        assertThat(run.get("failure").get("reason").asString()).isEqualTo("approval_rejected");
        assertThat(run.get("approval").get("decision").asString()).isEqualTo("rejected");
        assertThat(queued()).isEqualTo(before);
    }

    @Test
    void secondDecisionIs409() throws Exception {
        var runId = waitingRun("decide-twice");
        decide(runId, "approved", 7);

        var again = decide(runId, "rejected", 7);

        assertThat(again.statusCode()).isEqualTo(409);
        assertThat(ApiClient.json(api.get("/runs/" + runId)).get("status").asString()).isEqualTo("QUEUED");
    }

    @Test
    void wrongStepIs409() throws Exception {
        var runId = waitingRun("wrong-step");

        var response = decide(runId, "approved", 3);

        assertThat(response.statusCode()).isEqualTo(409);
        assertThat(response.body()).contains("step 7");
        assertThat(ApiClient.json(api.get("/runs/" + runId)).get("status").asString()).isEqualTo("WAITING_APPROVAL");
    }

    @Test
    void runNotWaitingIs409() throws Exception {
        api.publishExampleAgent("not-waiting");
        var runId = ApiClient.json(api.postJson("/runs", "{\"agent\":\"not-waiting\"}")).get("run_id").asString();

        assertThat(decide(runId, "approved", 1).statusCode()).isEqualTo(409);
    }

    @Test
    void badBodiesAre400() throws Exception {
        var runId = waitingRun("bad-approval");

        assertThat(decide(runId, "maybe", 7).statusCode()).isEqualTo(400);
        assertThat(decide(runId, "approved", 0).statusCode()).isEqualTo(400);
        assertThat(api.postJson("/runs/" + runId + "/approval", "{\"decision\":\"approved\"}").statusCode())
                .isEqualTo(400);
    }

    @Test
    void unknownRunIs404() throws Exception {
        assertThat(decide("run_missing", "approved", 1).statusCode()).isEqualTo(404);
    }

    @Test
    void approvalNeedsTheApiKey() throws Exception {
        var runId = waitingRun("approval-key");

        var response = api.send("POST", "/runs/" + runId + "/approval", "{\"decision\":\"approved\",\"step\":7}",
                "application/json", false);

        assertThat(response.statusCode()).isEqualTo(401);
    }
}
