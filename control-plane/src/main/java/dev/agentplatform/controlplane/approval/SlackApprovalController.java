package dev.agentplatform.controlplane.approval;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.trigger.WebhookRejectedException;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.time.Clock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * {@code POST /approvals/slack}: Slack's interactivity Request URL. No API key; only a valid
 * Slack signature is trusted ({@link SlackInteractions}). 503 if {@code SLACK_SIGNING_SECRET} is
 * unset, 401 for a bad or old signature, 400 for a malformed body. A verified request always gets
 * an empty 200 (Slack shows the clicker an error otherwise), within Slack's 3 seconds: the
 * decision is one conditional write and the message update has a 1.5 s timeout.
 *
 * <p>The body is read from the servlet input stream, not with {@code @RequestBody}: for a form
 * POST Spring rebuilds the body from the parsed parameters, re-encoded by Java (recorded: Slack's
 * real clicks then failed the signature). Nothing may read parameters before this method (no
 * filter calls {@code getParameter} on this path).
 */
@RestController
public class SlackApprovalController {

    private static final Logger log = LoggerFactory.getLogger(SlackApprovalController.class);

    private final ApprovalClicks clicks;
    private final Clock clock;
    private final SecretSource secrets;
    private final String approvalChannel;

    public SlackApprovalController(ApprovalClicks clicks, Clock clock, PlatformProperties props, SecretSource secrets) {
        this.clicks = clicks;
        this.clock = clock;
        this.secrets = secrets;
        this.approvalChannel = props.slackApprovalChannel();
    }

    @PostMapping("/approvals/slack")
    public ResponseEntity<Void> receive(
            @RequestHeader(value = "X-Slack-Request-Timestamp", required = false) String timestamp,
            @RequestHeader(value = "X-Slack-Signature", required = false) String signature,
            HttpServletRequest request) throws IOException {
        var signingSecret = secrets.get(Secret.SLACK_SIGNING_SECRET);
        if (signingSecret == null) {
            throw new WebhookRejectedException(HttpStatus.SERVICE_UNAVAILABLE,
                    "Slack interactivity is not configured (SLACK_SIGNING_SECRET)");
        }
        var raw = request.getInputStream().readAllBytes();
        if (!SlackInteractions.signatureMatches(signingSecret, timestamp, raw, signature, clock.instant())) {
            log.warn("Slack request rejected: missing, invalid or expired signature");
            throw new WebhookRejectedException(HttpStatus.UNAUTHORIZED,
                    "Missing, invalid or expired X-Slack-Signature");
        }
        SlackInteractions.Click click;
        try {
            var parsed = SlackInteractions.click(raw);
            if (parsed.isEmpty()) {
                return ResponseEntity.ok().build(); // another kind of interaction: nothing to do
            }
            click = parsed.get();
        } catch (IllegalArgumentException | tools.jackson.core.JacksonException e) {
            throw new WebhookRejectedException(HttpStatus.BAD_REQUEST, "Malformed Slack payload: " + e.getMessage());
        }
        if (approvalChannel != null && !approvalChannel.equals(click.message().channel())) {
            log.warn("Approval click from channel {} ignored (approvals live in {})", click.message().channel(),
                    approvalChannel);
            return ResponseEntity.ok().build();
        }
        clicks.apply(click);
        return ResponseEntity.ok().build();
    }
}
