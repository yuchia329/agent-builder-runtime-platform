package dev.agentplatform.controlplane.internal;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.contract.ContractValidator;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.annotation.DirtiesContext;

/**
 * Tenant limits over HTTP (ADR 0026, ADR 0027): the 429 at the cap matches the contract's
 * DeferredProblem, defer sets a run aside, and over the daily spend cap {@code POST /runs} is 429.
 * One run at once and a $0.01 daily cap for the configured tenant.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-limits",
        "platform.tenant.api-key=" + ApiClient.API_KEY,
        "platform.tenant.max-running=1",
        "platform.tenant.daily-usd=0.01"})
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)
class TenantLimitsApiIntegrationTest {

    private final ContractValidator contracts = new ContractValidator();

    @Value("${local.server.port}")
    int port;

    ApiClient api;

    @BeforeEach
    void setUp() throws Exception {
        api = new ApiClient(port);
        api.publishExampleAgent("limits");
    }

    private String startRun() throws Exception {
        var response = api.postJson("/runs", "{\"agent\":\"limits\",\"input\":{}}");
        assertThat(response.statusCode()).isEqualTo(201);
        return ApiClient.json(response).get("run_id").asString();
    }

    private HttpResponse<String> patch(String runId, String body) throws Exception {
        return api.send("PATCH", "/internal/runs/" + runId, body, "application/json", false);
    }

    private HttpResponse<String> defer(String runId, String body) throws Exception {
        return api.send("POST", "/internal/runs/" + runId + "/defer", body, "application/json", false);
    }

    @Test
    void startAtTheCapIs429DeferredWithRetryAfter() throws Exception {
        var first = startRun();
        var second = startRun();
        assertThat(patch(first, "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(200);

        var refused = patch(second, "{\"status\":\"RUNNING\"}");

        assertThat(refused.statusCode()).isEqualTo(429);
        var problem = ApiClient.json(refused);
        assertThat(contracts.validate(ContractValidator.DEFERRED_PROBLEM, problem)).isEmpty();
        assertThat(problem.get("reason").asString()).isEqualTo("tenant_running_limit");
        assertThat(refused.headers().firstValue("Retry-After"))
                .contains(Integer.toString(problem.get("retry_after_seconds").asInt()));
        assertThat(ApiClient.json(api.get("/runs/" + second)).get("status").asString()).isEqualTo("QUEUED");
    }

    @Test
    void deferSetsTheRunAsideAndMakesRoom() throws Exception {
        var first = startRun();
        var second = startRun();
        patch(first, "{\"status\":\"RUNNING\"}");

        var aside = defer(first, "{\"reason\":\"rate_limited\",\"retry_after_seconds\":60}");

        assertThat(aside.statusCode()).isEqualTo(200);
        var body = ApiClient.json(aside);
        assertThat(contracts.validate(ContractValidator.RUN_SUMMARY, body)).isEmpty();
        assertThat(body.get("status").asString()).isEqualTo("QUEUED");
        assertThat(patch(second, "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(200);
        assertThat(defer(first, "{\"reason\":\"rate_limited\",\"retry_after_seconds\":60}").statusCode())
                .isEqualTo(409); // QUEUED now, not RUNNING
    }

    @Test
    void deferBodyBreakingTheContractIs400() throws Exception {
        var run = startRun();
        patch(run, "{\"status\":\"RUNNING\"}");

        assertThat(defer(run, "{\"reason\":\"rate_limited\",\"retry_after_seconds\":901}").statusCode()).isEqualTo(400);
        assertThat(defer(run, "{\"reason\":\"bored\",\"retry_after_seconds\":5}").statusCode()).isEqualTo(400);
        assertThat(defer("run_missing", "{\"reason\":\"rate_limited\",\"retry_after_seconds\":5}").statusCode())
                .isEqualTo(404);
    }

    @Test
    void overTheDailyCapNewRunsAre429AndQueuedRunsFailAtStart() throws Exception {
        var spender = startRun();
        var waiting = startRun();
        patch(spender, "{\"status\":\"RUNNING\",\"cost_usd\":0.02}");

        var refused = api.postJson("/runs", "{\"agent\":\"limits\",\"input\":{}}");
        assertThat(refused.statusCode()).isEqualTo(429);
        assertThat(ApiClient.json(refused).get("reason").asString()).isEqualTo("tenant_budget");
        assertThat(refused.headers().firstValue("Retry-After")).isPresent();

        patch(spender, "{\"status\":\"SUCCEEDED\",\"output\":\"done\"}");
        assertThat(patch(waiting, "{\"status\":\"RUNNING\"}").statusCode()).isEqualTo(409);
        var failed = ApiClient.json(api.get("/runs/" + waiting));
        assertThat(failed.get("status").asString()).isEqualTo("FAILED");
        assertThat(failed.get("failure").get("reason").asString()).isEqualTo("tenant_budget");
    }
}
