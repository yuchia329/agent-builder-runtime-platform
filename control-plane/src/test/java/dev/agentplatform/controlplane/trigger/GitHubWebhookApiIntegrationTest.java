package dev.agentplatform.controlplane.trigger;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.AgentStore;
import dev.agentplatform.controlplane.store.RunStore;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.json.JsonMapper;

/**
 * POST /triggers/github, in-memory store and queue. Each test uses its own trigger action so
 * agents published by other tests do not match.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY,
        "platform.github.webhook-secret=" + GitHubWebhookApiIntegrationTest.SECRET})
class GitHubWebhookApiIntegrationTest {

    static final String SECRET = "webhook-test-secret";
    private static final JsonMapper JSON = JsonMapper.builder().build();

    @Value("${local.server.port}")
    int port;

    @Autowired
    RunQueuePublisher publisher;

    @Autowired
    RunStore runStore;

    @Autowired
    AgentStore agentStore;

    ApiClient api;
    HttpClient http = HttpClient.newHttpClient();

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
    }

    @Test
    void issueEventStartsARunPerMatchingAgentWithTrimmedInput() throws Exception {
        publishAgent("triage-a", "github.issue.opened");
        publishAgent("triage-b", "github.issue.opened");
        publishAgent("on-close", "github.issue.closed");
        var queued = queueSize();

        var response = deliver("issues", issuesPayload("opened"));

        assertThat(response.statusCode()).isEqualTo(202);
        var runIds = new ArrayList<String>();
        ApiClient.json(response).get("runs").forEach(id -> runIds.add(id.asString()));
        var started = runIds.stream().map(id -> runStore.find(id).orElseThrow()).toList();
        assertThat(started).extracting(r -> r.agentName()).contains("triage-a", "triage-b").doesNotContain("on-close");
        assertThat(queueSize()).isEqualTo(queued + runIds.size());

        var run = started.stream().filter(r -> r.agentName().equals("triage-a")).findFirst().orElseThrow();
        var input = JSON.readTree(run.inputJson());
        assertThat(input.at("/repository/full_name").asString()).isEqualTo("octocat/Hello-World");
        assertThat(input.at("/issue/number").asInt()).isEqualTo(4);
        assertThat(input.has("sender")).isTrue();
        assertThat(input.at("/repository/forks_url").isMissingNode()).isTrue();
    }

    @Test
    void eventNoAgentListensToStartsNothing() throws Exception {
        var queued = queueSize();

        var response = deliver("issues", issuesPayload("transferred"));

        assertThat(response.statusCode()).isEqualTo(202);
        assertThat(ApiClient.json(response).get("runs")).isEmpty();
        assertThat(queueSize()).isEqualTo(queued);
    }

    @Test
    void otherTenantsAgentsAreNeverMatched() throws Exception {
        agentStore.createAgent("tenant-other", "their-triage");
        agentStore.publishVersion("tenant-other", "their-triage", "github.issue.reopened", "{}");
        publishAgent("our-triage", "github.issue.reopened");

        var response = deliver("issues", issuesPayload("reopened"));

        var agentNames = new ArrayList<String>();
        ApiClient.json(response).get("runs")
                .forEach(id -> agentNames.add(runStore.find(id.asString()).orElseThrow().agentName()));
        assertThat(agentNames).containsExactly("our-triage");
    }

    @Test
    void pingIsAnsweredWithoutRuns() throws Exception {
        var queued = queueSize();

        var response = deliver("ping", "{\"zen\":\"Keep it logically awesome.\",\"hook_id\":1}");

        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(ApiClient.json(response).get("runs")).isEmpty();
        assertThat(queueSize()).isEqualTo(queued);
    }

    @Test
    void badOrMissingSignatureIs401AndStartsNothing() throws Exception {
        publishAgent("unsigned", "github.issue.edited");
        var body = issuesPayload("edited");
        var queued = queueSize();

        var wrong = send("issues", body, GitHubWebhooks.sign("not-the-secret", bytes(body)));
        var missing = send("issues", body, null);
        var tampered = send("issues", body.replace("EUR", "USD"), GitHubWebhooks.sign(SECRET, bytes(body)));

        assertThat(wrong.statusCode()).isEqualTo(401);
        assertThat(missing.statusCode()).isEqualTo(401);
        assertThat(tampered.statusCode()).isEqualTo(401);
        assertThat(wrong.headers().firstValue("Content-Type")).hasValueSatisfying(
                type -> assertThat(type).startsWith("application/problem+json"));
        assertThat(queueSize()).isEqualTo(queued);
    }

    @Test
    void missingEventHeaderOrNonObjectPayloadIs400() throws Exception {
        assertThat(deliver(null, "{}").statusCode()).isEqualTo(400);
        assertThat(deliver("issues", "[1,2]").statusCode()).isEqualTo(400);
        assertThat(deliver("issues", "payload=%7B%7D").statusCode()).isEqualTo(400);
    }

    @Test
    void needsNoApiKey() throws Exception {
        assertThat(deliver("ping", "{}").statusCode()).isEqualTo(200);
    }

    private void publishAgent(String name, String trigger) throws Exception {
        api.postJson("/agents", "{\"name\":\"%s\"}".formatted(name));
        var spec = Files.readString(Path.of("..", "contracts", "examples", "agent-spec.valid.yaml"))
                .replace("issue-triage", name)
                .replace("trigger: github.issue.opened", "trigger: " + trigger);
        var response = api.send("POST", "/agents/" + name + "/versions", spec, "application/yaml", true);
        assertThat(response.statusCode()).isEqualTo(201);
    }

    private static String issuesPayload(String action) throws Exception {
        return Files.readString(Path.of("src/test/resources/github/issues-opened.json"))
                .replace("\"action\": \"opened\"", "\"action\": \"" + action + "\"");
    }

    private HttpResponse<String> deliver(String event, String body) throws Exception {
        return send(event, body, GitHubWebhooks.sign(SECRET, bytes(body)));
    }

    /** Sends like GitHub: JSON body, event and delivery headers, optional signature, no API key. */
    private HttpResponse<String> send(String event, String body, String signature) throws Exception {
        var request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/triggers/github"))
                .POST(HttpRequest.BodyPublishers.ofByteArray(bytes(body)))
                .header("Content-Type", "application/json")
                .header("X-GitHub-Delivery", "delivery-test");
        if (event != null) {
            request.header("X-GitHub-Event", event);
        }
        if (signature != null) {
            request.header("X-Hub-Signature-256", signature);
        }
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private int queueSize() {
        return ((InMemoryRunQueuePublisher) publisher).bodies().size();
    }

    private static byte[] bytes(String body) {
        return body.getBytes(StandardCharsets.UTF_8);
    }
}
