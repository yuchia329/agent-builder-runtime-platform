package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** Public run endpoints, in-memory store and queue. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class RunApiIntegrationTest {

    @Value("${local.server.port}")
    int port;

    @Autowired
    RunQueuePublisher publisher;

    ApiClient api;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
    }

    @Test
    void startingARunStoresItQueuedAndEnqueuesIt() throws Exception {
        api.publishExampleAgent("start-run");
        var before = ((InMemoryRunQueuePublisher) publisher).bodies().size();

        var response = api.postJson("/runs", "{\"agent\":\"start-run\",\"input\":{\"issue\":{\"number\":42}}}");

        assertThat(response.statusCode()).isEqualTo(201);
        var run = ApiClient.json(response);
        var runId = run.get("run_id").asString();
        assertThat(runId).matches("^run_[0-9a-f]{32}$");
        assertThat(run.get("status").asString()).isEqualTo("QUEUED");
        assertThat(run.get("agent_version").asInt()).isEqualTo(1);
        assertThat(run.get("tenant_id").asString()).isEqualTo("tenant-test");
        assertThat(response.headers().firstValue("Location")).hasValue("/runs/" + runId);

        var bodies = ((InMemoryRunQueuePublisher) publisher).bodies();
        assertThat(bodies).hasSize(before + 1);
        assertThat(bodies.getLast()).contains("\"run_id\":\"" + runId + "\"");

        var fetched = api.get("/runs/" + runId);
        assertThat(fetched.statusCode()).isEqualTo(200);
        assertThat(ApiClient.json(fetched).get("status").asString()).isEqualTo("QUEUED");
    }

    @Test
    void inputDefaultsToEmptyObject() throws Exception {
        api.publishExampleAgent("no-input");

        assertThat(api.postJson("/runs", "{\"agent\":\"no-input\"}").statusCode()).isEqualTo(201);
    }

    @Test
    void nonObjectInputIs400() throws Exception {
        api.publishExampleAgent("bad-input");

        assertThat(api.postJson("/runs", "{\"agent\":\"bad-input\",\"input\":[1]}").statusCode()).isEqualTo(400);
    }

    @Test
    void unknownAgentIs404() throws Exception {
        assertThat(api.postJson("/runs", "{\"agent\":\"never-created\"}").statusCode()).isEqualTo(404);
    }

    @Test
    void unpublishedAgentIs409() throws Exception {
        api.postJson("/agents", "{\"name\":\"draft-only\"}");

        assertThat(api.postJson("/runs", "{\"agent\":\"draft-only\"}").statusCode()).isEqualTo(409);
    }

    @Test
    void unknownRunIs404() throws Exception {
        assertThat(api.get("/runs/run_missing").statusCode()).isEqualTo(404);
    }

    @Test
    void runsNeedTheApiKey() throws Exception {
        assertThat(api.send("POST", "/runs", "{\"agent\":\"x\"}", "application/json", false).statusCode())
                .isEqualTo(401);
        assertThat(api.send("GET", "/runs/run_x", null, null, false).statusCode()).isEqualTo(401);
    }

    @Test
    void responsesUseSnakeCase() throws Exception {
        var response = api.postJson("/agents", "{\"name\":\"snake-case\"}");

        assertThat(response.body()).contains("\"latest_version\":0").doesNotContain("latestVersion");
    }
}
