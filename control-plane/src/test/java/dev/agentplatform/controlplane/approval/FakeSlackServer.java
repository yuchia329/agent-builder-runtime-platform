package dev.agentplatform.controlplane.approval;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Local HTTP server answering like the Slack Web API with recorded (cleaned) replies from
 * {@code src/test/resources/fixtures/slack/}. Records every request. Deterministic; one per test.
 */
class FakeSlackServer implements AutoCloseable {

    static final JsonMapper JSON = JsonMapper.builder().build();

    record Request(String method, String path, Map<String, List<String>> headers, JsonNode body) {
        String header(String name) {
            return headers.entrySet().stream().filter(e -> e.getKey().equalsIgnoreCase(name))
                    .map(e -> e.getValue().getFirst()).findFirst().orElse(null);
        }
    }

    private record Reply(int status, String body, Map<String, String> headers) {}

    private final HttpServer server;
    private final List<Request> requests = new ArrayList<>();
    private Reply reply = new Reply(200, fixture("post_message"), Map.of());

    FakeSlackServer() {
        try {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        server.createContext("/api/", exchange -> {
            var body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Reply current;
            synchronized (this) {
                requests.add(new Request(exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                        Map.copyOf(exchange.getRequestHeaders()), body.isEmpty() ? null : JSON.readTree(body)));
                current = reply;
            }
            current.headers().forEach((k, v) -> exchange.getResponseHeaders().add(k, v));
            exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
            var bytes = current.body().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(current.status(), bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    /** Base URL to pass as the Slack API URL. */
    String apiUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + "/api";
    }

    /** Answers every following request with {@code body} (a fixture's text) and HTTP 200. */
    synchronized void answer(String body) {
        reply = new Reply(200, body, Map.of());
    }

    synchronized void answer(int status, String body, Map<String, String> headers) {
        reply = new Reply(status, body, headers);
    }

    synchronized List<Request> requests() {
        return List.copyOf(requests);
    }

    static String fixture(String name) {
        try {
            return Files.readString(Path.of("src/test/resources/fixtures/slack/" + name + ".json"));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
