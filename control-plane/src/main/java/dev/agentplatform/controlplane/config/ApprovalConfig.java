package dev.agentplatform.controlplane.config;

import dev.agentplatform.controlplane.approval.ApprovalChannel;
import dev.agentplatform.controlplane.approval.InMemoryApprovalChannel;
import dev.agentplatform.controlplane.approval.SlackApprovalChannel;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Picks the {@link ApprovalChannel}: Slack when {@code SLACK_BOT_TOKEN} is set up (then
 * {@code SLACK_APPROVAL_CHANNEL} is required; with SSM secrets it always is, ADR 0018), otherwise
 * in memory (requests are only logged; decide with {@code POST /runs/{id}/approval}). The token is
 * read from the {@link SecretSource} on each call, not at startup.
 */
@Configuration(proxyBeanMethods = false)
public class ApprovalConfig {

    private static final Logger log = LoggerFactory.getLogger(ApprovalConfig.class);

    @Bean
    ApprovalChannel approvalChannel(PlatformProperties props, SecretSource secrets) {
        if (!secrets.configured(Secret.SLACK_BOT_TOKEN)) {
            log.info("SLACK_BOT_TOKEN not set: approval requests are logged, not posted");
            return new InMemoryApprovalChannel();
        }
        var channel = props.slackApprovalChannel();
        if (channel == null) {
            throw new IllegalStateException("SLACK_APPROVAL_CHANNEL must be set when SLACK_BOT_TOKEN is set");
        }
        return new SlackApprovalChannel(props.slackApiUrl(), () -> secrets.get(Secret.SLACK_BOT_TOKEN), channel);
    }
}
