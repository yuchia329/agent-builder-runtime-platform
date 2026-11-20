package dev.agentplatform.controlplane.trace;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunMessage;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.data.SpanData;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;

/** One trace per run, started by the control plane and carried by the queue message (ADR 0022). */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class TracingIntegrationTest {

    private static final InMemorySpanExporter SPANS = InMemorySpanExporter.create();
    private static final String SECRET_INPUT = "do-not-trace-this-issue-text";

    @TestConfiguration
    static class Spans {
        @Bean
        @Primary
        TraceExport testTraceExport() {
            return new TraceExport(() -> SPANS);
        }
    }

    @Value("${local.server.port}")
    int port;

    @Autowired
    RunQueuePublisher queue;

    ApiClient api;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
    }

    private String startRun(String agent) throws Exception {
        api.publishExampleAgent(agent);
        SPANS.reset();
        var response = api.postJson("/runs",
                "{\"agent\":\"%s\",\"input\":{\"issue\":{\"title\":\"%s\"}}}".formatted(agent, SECRET_INPUT));
        assertThat(response.statusCode()).isEqualTo(201);
        return ApiClient.json(response).get("run_id").asString();
    }

    private RunMessage lastMessage(String runId) {
        var messages = ((InMemoryRunQueuePublisher) queue).messages().stream()
                .filter(m -> m.runId().equals(runId)).toList();
        return messages.getLast();
    }

    private static SpanData span(String name) {
        var found = SPANS.getFinishedSpanItems().stream().filter(s -> s.getName().equals(name)).toList();
        assertThat(found).as("spans named %s in %s", name, SPANS.getFinishedSpanItems()).hasSize(1);
        return found.getFirst();
    }

    @Test
    void startingARunOpensItsTraceAndTheQueueMessageCarriesIt() throws Exception {
        var runId = startRun("trace-start");

        var traceId = ApiClient.json(api.get("/runs/" + runId)).get("trace_id").asString();
        var start = span("run.start");
        assertThat(start.getTraceId()).isEqualTo(traceId);
        assertThat(start.getParentSpanContext().isValid()).isFalse();
        assertThat(start.getAttributes().get(RunTracing.RUN)).isEqualTo(runId);
        assertThat(start.getAttributes().get(RunTracing.TENANT)).isEqualTo("tenant-test");
        assertThat(start.getAttributes().get(RunTracing.AGENT)).isEqualTo("trace-start");
        assertThat(start.getAttributes().get(RunTracing.VERSION)).isEqualTo(1L);
        assertThat(lastMessage(runId).traceParent())
                .isEqualTo("00-" + traceId + "-" + start.getSpanId() + "-01");

        // The request is its own trace, linked from the run's.
        var request = span("POST /runs");
        assertThat(request.getKind()).isEqualTo(SpanKind.SERVER);
        assertThat(request.getTraceId()).isNotEqualTo(traceId);
        assertThat(request.getAttributes().get(TracingFilter.STATUS)).isEqualTo(201L);
        assertThat(start.getLinks()).anySatisfy(link ->
                assertThat(link.getSpanContext().getSpanId()).isEqualTo(request.getSpanId()));

        // Store and queue calls ran inside the run span (in memory here, so no AWS spans).
        assertThat(SPANS.getFinishedSpanItems()).allSatisfy(s ->
                assertThat(s.getAttributes().asMap().values()).noneSatisfy(v ->
                        assertThat(v.toString()).contains(SECRET_INPUT)));
    }

    @Test
    void workerCallsJoinTheRunsTrace() throws Exception {
        var runId = startRun("trace-internal");
        var traceParent = lastMessage(runId).traceParent();
        var parent = RunTracing.parse(traceParent);
        SPANS.reset();

        var response = api.send("GET", "/internal/runs/" + runId, null, null, false,
                Map.of("traceparent", traceParent));

        assertThat(response.statusCode()).isEqualTo(200);
        var server = span("GET /internal/runs/{runId}");
        assertThat(server.getTraceId()).isEqualTo(parent.getTraceId());
        assertThat(server.getParentSpanId()).isEqualTo(parent.getSpanId());
        assertThat(server.getAttributes().get(RunTracing.RUN)).isEqualTo(runId);
        assertThat(server.getAttributes().get(TracingFilter.ROUTE)).isEqualTo("/internal/runs/{runId}");
    }

    @Test
    void publicCallersCannotJoinATrace() throws Exception {
        var runId = startRun("trace-public");
        var traceParent = lastMessage(runId).traceParent();
        SPANS.reset();

        api.send("GET", "/runs/" + runId, null, null, true, Map.of("traceparent", traceParent));

        var server = span("GET /runs/{runId}");
        assertThat(server.getTraceId()).isNotEqualTo(RunTracing.parse(traceParent).getTraceId());
        assertThat(server.getParentSpanContext().isValid()).isFalse();
    }

    @Test
    void anApprovedRunIsReEnqueuedInItsOwnTrace() throws Exception {
        var runId = startRun("trace-approval");
        var traceId = RunTracing.parse(lastMessage(runId).traceParent()).getTraceId();
        var startSpanId = span("run.start").getSpanId();
        patch(runId, "{\"status\":\"RUNNING\"}");
        patch(runId, "{\"status\":\"WAITING_APPROVAL\",\"pending_approval\":"
                + "{\"step\":3,\"tool\":\"github.close_issue\",\"arguments\":{\"issue_number\":42}}}");
        SPANS.reset();

        var decided = api.postJson("/runs/" + runId + "/approval", "{\"decision\":\"approved\",\"step\":3}");

        assertThat(decided.statusCode()).isEqualTo(200);
        var approval = span("run.approval");
        assertThat(approval.getTraceId()).isEqualTo(traceId);
        assertThat(approval.getParentSpanId()).isEqualTo(startSpanId);
        assertThat(approval.getAttributes().asMap().keySet().stream().map(Object::toString))
                .contains("approval.decision", "approval.step");
        assertThat(lastMessage(runId).traceParent())
                .isEqualTo("00-" + traceId + "-" + approval.getSpanId() + "-01");
    }

    @Test
    void aWorkersTraceIdDoesNotReplaceTheRunsOwn() throws Exception {
        var runId = startRun("trace-patch");
        var traceId = ApiClient.json(api.get("/runs/" + runId)).get("trace_id").asString();

        patch(runId, "{\"status\":\"RUNNING\",\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}");

        assertThat(ApiClient.json(api.get("/runs/" + runId)).get("trace_id").asString()).isEqualTo(traceId);
    }

    @Test
    void requestsWithoutRunsStillGetServerSpans() throws Exception {
        SPANS.reset();

        var response = api.send("GET", "/runs/nope", null, null, false);

        assertThat(response.statusCode()).isEqualTo(401);
        assertThat(SPANS.getFinishedSpanItems()).extracting(SpanData::getKind).containsExactly(SpanKind.SERVER);
        assertThat(SPANS.getFinishedSpanItems().getFirst().getAttributes().get(TracingFilter.STATUS)).isEqualTo(401L);
    }

    private void patch(String runId, String body) throws Exception {
        var response = api.send("PATCH", "/internal/runs/" + runId, body, "application/json", false);
        assertThat(response.statusCode()).isEqualTo(200);
    }
}
