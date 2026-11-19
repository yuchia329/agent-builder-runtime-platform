package dev.agentplatform.controlplane.lambda;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.amazonaws.serverless.proxy.internal.LambdaContainerHandler;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.store.RunStore;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

/**
 * The Lambda entry without AWS: HTTP API events reach the Spring app, a sweep event runs the
 * sweeps, and priming stops before any store call. The app starts once per JVM (static
 * initializer, as on Lambda), configured through system properties set before the class loads.
 */
class LambdaHandlerTest {

    private static final String KEY = "lambda-test-key";

    private static LambdaHandler handler;

    @BeforeAll
    static void start() {
        System.setProperty("platform.tenant.api-key", KEY);
        System.setProperty("platform.slack.signing-secret", "lambda-test-slack-secret");
        System.setProperty("platform.github.webhook-secret", "lambda-test-github-secret");
        System.setProperty("platform.sweep.queued-after-seconds", "0");
        handler = new LambdaHandler();
    }

    @Test
    void httpApiEventReachesTheApp() throws IOException {
        var created = invoke(HttpEvents.request("POST", "/agents",
                Map.of("Content-Type", "application/json", "X-Api-Key", KEY), "{\"name\":\"lambda-agent\"}"));
        assertThat(created.path("statusCode").asInt()).isEqualTo(201);
        assertThat(created.path("body").asString()).contains("\"name\":\"lambda-agent\"");

        var missing = invoke(HttpEvents.request("GET", "/runs/run_missing", Map.of("X-Api-Key", KEY), null));
        assertThat(missing.path("statusCode").asInt()).isEqualTo(404);

        var noKey = invoke(HttpEvents.request("GET", "/runs/run_missing", Map.of(), null));
        assertThat(noKey.path("statusCode").asInt()).isEqualTo(401);
    }

    @Test
    void sweepEventRunsTheSweeps() throws IOException {
        var run = Run.queued("run_" + "c".repeat(32), "tenant-demo", "triage", 1, "{}",
                Instant.now().minusSeconds(5));
        handler.bean(RunStore.class).create(run); // saved QUEUED, never enqueued

        var result = invoke("{\"sweep\":\"all\"}".getBytes(StandardCharsets.UTF_8));

        assertThat(result.path("requeued").asInt()).isGreaterThanOrEqualTo(1);
        assertThat(result.has("posted")).isTrue();
        assertThat(result.has("expired")).isTrue();
        var queue = (InMemoryRunQueuePublisher) handler.bean(RunQueuePublisher.class);
        assertThat(queue.bodies()).anyMatch(b -> b.contains(run.runId()));
    }

    @Test
    void unknownSweepIsRefused() {
        assertThatThrownBy(() -> invoke("{\"sweep\":\"everything\"}".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("only \"all\"");
    }

    @Test
    void primingStopsBeforeAnyStoreCall() {
        // Slack non-click 200, GitHub ping 200, invalid run body 400, no API key 401.
        assertThat(Priming.run(handler)).containsExactly(200, 200, 400, 401);
    }

    @Test
    void inProcessScheduleIsOffOnLambda() {
        assertThat(System.getProperty("platform.sweep.interval-seconds")).isEqualTo("0");
    }

    private static JsonNode invoke(byte[] event) throws IOException {
        var out = new ByteArrayOutputStream();
        handler.handleRequest(new ByteArrayInputStream(event), out, new LocalContext());
        return LambdaContainerHandler.getObjectMapper().readTree(out.toByteArray());
    }
}
