package dev.agentplatform.controlplane.trigger;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ApiClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** Without GITHUB_WEBHOOK_SECRET the route refuses every delivery, signed or not. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "platform.tenant.id=tenant-test",
        "platform.tenant.api-key=" + ApiClient.API_KEY,
        "platform.github.webhook-secret="})
class GitHubWebhookNotConfiguredIntegrationTest {

    @Value("${local.server.port}")
    int port;

    @Test
    void deliveriesAre503() throws Exception {
        var response = new ApiClient(port).send("POST", "/triggers/github", "{}", "application/json", false);

        assertThat(response.statusCode()).isEqualTo(503);
    }
}
