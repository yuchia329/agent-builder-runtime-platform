package dev.agentplatform.controlplane.trigger;

import dev.agentplatform.controlplane.tenant.Principals;
import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.store.DeliveryStore;
import java.time.Clock;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * GitHub webhook receiver. No API key: the request is trusted only if its
 * {@code X-Hub-Signature-256} matches the configured secret. Starts one run per agent of the
 * configured tenant whose trigger matches the event (until the GitHub App maps installations to
 * tenants in M4).
 *
 * <p>Redeliveries: the {@code X-GitHub-Delivery} id is claimed before any run starts. A delivery
 * seen before gets 200 with the first delivery's runs and {@code duplicate: true}, and starts
 * nothing. If handling fails, the claim is released so GitHub's redelivery can try again, except
 * when only the enqueue failed: those runs are saved QUEUED and the sweep sends them.
 */
@RestController
public class GitHubWebhookController {

    private static final Logger log = LoggerFactory.getLogger(GitHubWebhookController.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();

    private static final String SOURCE = "github";

    private final RunService runs;
    private final DeliveryStore deliveries;
    private final Clock clock;
    private final String tenantId;
    private final String secret;

    public GitHubWebhookController(RunService runs, DeliveryStore deliveries, Clock clock, PlatformProperties props) {
        this.runs = runs;
        this.deliveries = deliveries;
        this.clock = clock;
        this.tenantId = props.tenant().id();
        this.secret = props.githubWebhookSecret();
    }

    /** Runs started for one delivery; {@code duplicate} is true (else absent) for a redelivery. */
    public record Started(List<String> runs, Boolean duplicate) {
        Started(List<String> runs) {
            this(runs, null);
        }
    }

    @PostMapping("/triggers/github")
    public ResponseEntity<Started> receive(
            @RequestHeader(value = "X-GitHub-Event", required = false) String event,
            @RequestHeader(value = "X-GitHub-Delivery", required = false) String delivery,
            @RequestHeader(value = "X-Hub-Signature-256", required = false) String signature,
            @RequestBody(required = false) byte[] body) {
        if (secret == null) {
            throw rejected(HttpStatus.SERVICE_UNAVAILABLE, "GitHub webhooks are not configured (GITHUB_WEBHOOK_SECRET)");
        }
        var raw = body == null ? new byte[0] : body;
        if (!GitHubWebhooks.signatureMatches(secret, raw, signature)) {
            log.warn("GitHub delivery {} rejected: missing or invalid signature", delivery);
            throw rejected(HttpStatus.UNAUTHORIZED, "Missing or invalid X-Hub-Signature-256");
        }
        if (event == null || event.isBlank()) {
            throw rejected(HttpStatus.BAD_REQUEST, "Missing X-GitHub-Event header");
        }
        if ("ping".equals(event)) {
            return ResponseEntity.ok(new Started(List.of()));
        }
        ObjectNode payload;
        try {
            if (!(JSON.readTree(raw) instanceof ObjectNode object)) {
                throw rejected(HttpStatus.BAD_REQUEST, "Payload must be a JSON object (content type application/json)");
            }
            payload = object;
        } catch (JacksonException e) {
            throw rejected(HttpStatus.BAD_REQUEST, "Payload must be a JSON object (content type application/json)");
        }

        var trigger = GitHubWebhooks.triggerName(event, payload);
        var input = JSON.writeValueAsString(GitHubWebhooks.runInput(event, payload));

        var claimed = delivery != null && !delivery.isBlank();
        if (claimed) {
            var previous = deliveries.claim(SOURCE, delivery, clock.instant());
            if (previous.isPresent()) {
                log.info("GitHub delivery {} ({}) seen before; runs {}, nothing started", delivery, trigger, previous.get());
                return ResponseEntity.ok(new Started(previous.get(), true));
            }
        } else {
            log.warn("GitHub {} event without X-GitHub-Delivery; handled without redelivery check", trigger);
        }

        List<String> started;
        try {
            started = runs.startForTrigger(tenantId, trigger, input, Principals.WEBHOOK_GITHUB).stream().map(Run::runId).toList();
        } catch (RunQueueException e) {
            throw e; // runs are saved QUEUED and the sweep sends them: keep the claim
        } catch (RuntimeException e) {
            if (claimed) {
                deliveries.release(SOURCE, delivery);
            }
            throw e;
        }
        if (claimed) {
            deliveries.recordRuns(SOURCE, delivery, started);
        }
        log.info("GitHub delivery {} ({}): started {} run(s) {}", delivery, trigger, started.size(), started);
        return ResponseEntity.accepted().body(new Started(started));
    }

    private static WebhookRejectedException rejected(HttpStatus status, String detail) {
        return new WebhookRejectedException(status, detail);
    }
}
