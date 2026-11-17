package dev.agentplatform.controlplane;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Small HTTP client for integration tests against the running app. */
public final class ApiClient {

    public static final String API_KEY = "test-key";
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final HttpClient http = HttpClient.newHttpClient();
    private final int port;

    public ApiClient(int port) {
        this.port = port;
    }

    public HttpResponse<String> send(String method, String path, String body, String contentType, boolean withKey)
            throws Exception {
        var builder = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
        if (contentType != null) {
            builder.header("Content-Type", contentType);
        }
        if (withKey) {
            builder.header("X-Api-Key", API_KEY);
        }
        return http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
    }

    public HttpResponse<String> postJson(String path, String json) throws Exception {
        return send("POST", path, json, "application/json", true);
    }

    public HttpResponse<String> get(String path) throws Exception {
        return send("GET", path, null, null, true);
    }

    /** Creates an agent and publishes the example spec under its name. */
    public void publishExampleAgent(String name) throws Exception {
        postJson("/agents", "{\"name\":\"%s\"}".formatted(name));
        var spec = Files.readString(Path.of("..", "contracts", "examples", "agent-spec.valid.yaml"))
                .replace("issue-triage", name);
        var response = send("POST", "/agents/" + name + "/versions", spec, "application/yaml", true);
        if (response.statusCode() != 201) {
            throw new IllegalStateException("Publish failed: " + response.body());
        }
    }

    public static JsonNode json(HttpResponse<String> response) {
        return JSON.readTree(response.body());
    }
}
