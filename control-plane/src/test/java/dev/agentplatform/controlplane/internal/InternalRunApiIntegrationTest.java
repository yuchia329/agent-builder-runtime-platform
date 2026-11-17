package dev.agentplatform.controlplane.internal;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** Internal run API, checked against contracts/internal-api.openapi.yaml. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class InternalRunApiIntegrationTest {

    private final ContractValidator contracts = new ContractValidator();

    @Value("${local.server.port}")
    int port;

    ApiClient api;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
    }

    private String startRun(String agent) throws Exception {
        api.publishExampleAgent(agent);
        var response = api.postJson("/runs", "{\"agent\":\"%s\",\"input\":{\"issue\":{\"number\":42}}}".formatted(agent));
        return ApiClient.json(response).get("run_id").asString();
    }

    private java.net.http.HttpResponse<String> patch(String runId, String body) throws Exception {
        // Internal routes carry no API key; API Gateway checks SigV4 in AWS.
        return api.send("PATCH", "/internal/runs/" + runId, body, "application/json", false);
    }

    @Test
    void getReturnsRunWithPinnedSpecMatchingContract() throws Exception {
        var runId = startRun("internal-get");

        var response = api.send("GET", "/internal/runs/" + runId, null, null, false);

        assertThat(response.statusCode()).isEqualTo(200);
        var run = ApiClient.json(response);
        assertThat(contracts.validate(ContractValidator.INTERNAL_RUN, run)).isEmpty();
        assertThat(run.get("spec").get("name").asString()).isEqualTo("internal-get");
        assertThat(run.get("input").get("issue").get("number").asInt()).isEqualTo(42);
        assertThat(run.has("run_token")).isFalse();
    }

    @Test
    void workerLifecycleWithApproval() throws Exception {
        var runId = startRun("internal-lifecycle");

        var running = patch(runId, "{\"status\":\"RUNNING\",\"steps\":1,\"cost_usd\":0.001,"
                + "\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}");
        assertThat(running.statusCode()).isEqualTo(200);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, ApiClient.json(running))).isEmpty();

        var waiting = patch(runId, "{\"status\":\"WAITING_APPROVAL\",\"steps\":6,\"pending_approval\":"
                + "{\"step\":7,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}}");
        assertThat(waiting.statusCode()).isEqualTo(200);
        var body = ApiClient.json(waiting);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, body)).isEmpty();
        assertThat(body.get("pending_approval").get("tool").asString()).isEqualTo("github.close_issue");
        assertThat(body.get("trace_id").asString()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");

        var internal = ApiClient.json(api.send("GET", "/internal/runs/" + runId, null, null, false));
        assertThat(contracts.validate(ContractValidator.INTERNAL_RUN, internal)).isEmpty();
        assertThat(internal.get("status").asString()).isEqualTo("WAITING_APPROVAL");
    }

    @Test
    void finishedRunCannotChangeAgain() throws Exception {
        var runId = startRun("internal-finish");
        patch(runId, "{\"status\":\"RUNNING\"}");

        var done = patch(runId, "{\"status\":\"SUCCEEDED\",\"output\":\"Labeled as bug.\"}");
        assertThat(done.statusCode()).isEqualTo(200);
        assertThat(ApiClient.json(done).get("output").asString()).isEqualTo("Labeled as bug.");

        assertThat(patch(runId, "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(409);
    }

    @Test
    void queuedRunCannotSkipRunning() throws Exception {
        var runId = startRun("internal-skip");

        var response = patch(runId, "{\"status\":\"SUCCEEDED\"}");

        assertThat(response.statusCode()).isEqualTo(409);
        assertThat(response.body()).contains("QUEUED");
    }

    @Test
    void bodyBreakingTheContractIs400WithErrors() throws Exception {
        var runId = startRun("internal-invalid");

        var response = patch(runId, "{\"status\":\"FAILED\",\"steps\":21}");

        assertThat(response.statusCode()).isEqualTo(400);
        assertThat(response.body()).contains("\"errors\"", "failure");
    }

    @Test
    void unknownRunIs404() throws Exception {
        assertThat(api.send("GET", "/internal/runs/run_missing", null, null, false).statusCode()).isEqualTo(404);
        assertThat(patch("run_missing", "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(404);
    }
}
