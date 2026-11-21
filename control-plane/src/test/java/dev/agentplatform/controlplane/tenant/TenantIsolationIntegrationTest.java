package dev.agentplatform.controlplane.tenant;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import dev.agentplatform.controlplane.store.TenantStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.math.BigDecimal;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** Two tenants on one app: the configured key and a stored key, each sees only its own data. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-a",
        "platform.tenant.api-key=" + ApiClient.API_KEY})
class TenantIsolationIntegrationTest {

    @Value("${local.server.port}")
    int port;

    @Autowired
    TenantStore tenants;

    ApiClient api;
    String keyB;

    @BeforeEach
    void setUp() {
        api = new ApiClient(port);
        var id = "tenant-b-" + System.nanoTime();
        tenants.create(new Tenant(id, new TenantLimits(5, BigDecimal.ONE), Instant.now()));
        var key = ApiKeys.generate();
        tenants.addApiKey(id, key.hash(), key.prefix(), Instant.now());
        keyB = key.key();
    }

    private HttpResponse<String> asB(String method, String path, String body, String type) throws Exception {
        return api.send(method, path, body, type, false, Map.of("X-Api-Key", keyB));
    }

    private void publishAsB(String name) throws Exception {
        asB("POST", "/agents", "{\"name\":\"%s\"}".formatted(name), "application/json");
        var spec = Files.readString(Path.of("..", "contracts", "examples", "agent-spec.valid.yaml"))
                .replace("issue-triage", name);
        assertThat(asB("POST", "/agents/" + name + "/versions", spec, "application/yaml").statusCode()).isEqualTo(201);
    }

    @Test
    void eachTenantSeesOnlyItsOwnRuns() throws Exception {
        api.publishExampleAgent("iso-a");
        var runA = ApiClient.json(api.postJson("/runs", "{\"agent\":\"iso-a\"}")).get("run_id").asString();

        assertThat(asB("GET", "/runs/" + runA, null, null).statusCode()).isEqualTo(404);
        assertThat(asB("POST", "/runs", "{\"agent\":\"iso-a\"}", "application/json").statusCode()).isEqualTo(404);

        publishAsB("iso-b");
        var started = asB("POST", "/runs", "{\"agent\":\"iso-b\"}", "application/json");
        assertThat(started.statusCode()).isEqualTo(201);
        var runB = ApiClient.json(started);
        assertThat(runB.get("tenant_id").asString()).startsWith("tenant-b-");
        assertThat(api.get("/runs/" + runB.get("run_id").asString()).statusCode()).isEqualTo(404);
    }

    @Test
    void bothTenantsCanUseTheSameAgentName() throws Exception {
        api.publishExampleAgent("shared-name");

        publishAsB("shared-name");

        assertThat(asB("POST", "/runs", "{\"agent\":\"shared-name\"}", "application/json").statusCode()).isEqualTo(201);
    }

    @Test
    void anUnknownKeyIs401() throws Exception {
        var unknown = ApiKeys.generate().key();
        var response = api.send("GET", "/runs/x", null, null, false, Map.of("X-Api-Key", unknown));

        assertThat(response.statusCode()).isEqualTo(401);
        assertThat(response.body()).doesNotContain(unknown);
    }
}
