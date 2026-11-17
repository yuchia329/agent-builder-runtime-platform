package dev.agentplatform.controlplane.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** Drives the real HTTP API with the in-memory store. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.store-backend=memory",
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=test-key"})
class AgentApiIntegrationTest {

    private final HttpClient http = HttpClient.newHttpClient();

    @Value("${local.server.port}")
    int port;

    @Test
    void createAgentThenPublishVersions() throws Exception {
        assertThat(createAgent("issue-triage").statusCode()).isEqualTo(201);

        var first = publish("issue-triage", SpecValidatorTest.example("agent-spec.valid.yaml"), "application/yaml");
        assertThat(first.statusCode()).isEqualTo(201);
        assertThat(first.headers().firstValue("Location")).hasValue("/agents/issue-triage/versions/1");
        assertThat(first.body()).contains("\"version\":1", "\"trigger\":\"github.issue.opened\"");

        var second = publish("issue-triage", SpecValidatorTest.example("agent-spec.valid.yaml"), "application/yaml");
        assertThat(second.body()).contains("\"version\":2");
    }

    @Test
    void invalidSpecIsRejectedWithErrors() throws Exception {
        createAgent("issue-triage-2");
        var body = SpecValidatorTest.example("agent-spec.invalid.yaml").replace("issue-triage", "issue-triage-2");

        var response = publish("issue-triage-2", body, "application/yaml");

        assertThat(response.statusCode()).isEqualTo(400);
        assertThat(response.headers().firstValue("Content-Type")).hasValueSatisfying(
                type -> assertThat(type).startsWith("application/problem+json"));
        assertThat(response.body()).contains("\"errors\"", "acts_as", "budget");
    }

    @Test
    void specNameMustMatchAgent() throws Exception {
        createAgent("other-agent");

        var response = publish("other-agent", SpecValidatorTest.example("agent-spec.valid.yaml"), "application/yaml");

        assertThat(response.statusCode()).isEqualTo(400);
        assertThat(response.body()).contains("does not match agent");
    }

    @Test
    void publishingToUnknownAgentIs404() throws Exception {
        var spec = SpecValidatorTest.example("agent-spec.valid.yaml").replace("issue-triage", "never-created");

        assertThat(publish("never-created", spec, "application/yaml").statusCode()).isEqualTo(404);
    }

    @Test
    void duplicateAgentIs409() throws Exception {
        createAgent("dup");

        assertThat(createAgent("dup").statusCode()).isEqualTo(409);
    }

    @Test
    void badAgentNameIs400() throws Exception {
        assertThat(createAgent("Bad Name").statusCode()).isEqualTo(400);
    }

    @Test
    void missingOrWrongApiKeyIs401() throws Exception {
        var noKey = HttpRequest.newBuilder(uri("/agents"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"name\":\"x1\"}")).build();
        var wrongKey = HttpRequest.newBuilder(uri("/agents"))
                .header("Content-Type", "application/json").header("X-Api-Key", "wrong")
                .POST(HttpRequest.BodyPublishers.ofString("{\"name\":\"x1\"}")).build();

        assertThat(http.send(noKey, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(401);
        assertThat(http.send(wrongKey, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(401);
    }

    private HttpResponse<String> createAgent(String name) throws Exception {
        return send("/agents", "{\"name\":\"%s\"}".formatted(name), "application/json");
    }

    private HttpResponse<String> publish(String agent, String body, String contentType) throws Exception {
        return send("/agents/" + agent + "/versions", body, contentType);
    }

    private HttpResponse<String> send(String path, String body, String contentType) throws Exception {
        var request = HttpRequest.newBuilder(uri(path))
                .header("Content-Type", contentType)
                .header("X-Api-Key", "test-key")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return http.send(request, HttpResponse.BodyHandlers.ofString());
    }

    private URI uri(String path) {
        return URI.create("http://127.0.0.1:" + port + path);
    }
}
