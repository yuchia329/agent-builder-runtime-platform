package dev.agentplatform.controlplane.config;

import dev.agentplatform.controlplane.approval.ApprovalChannel;
import dev.agentplatform.controlplane.approval.InMemoryApprovalChannel;
import dev.agentplatform.controlplane.approval.SlackApprovalChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Picks the {@link ApprovalChannel}: Slack when {@code SLACK_BOT_TOKEN} is set (then
 * {@code SLACK_APPROVAL_CHANNEL} is required), otherwise in memory (requests are only logged;
 * decide with {@code POST /runs/{id}/approval}).
 */
@Configuration(proxyBeanMethods = false)
public class ApprovalConfig {

    private static final Logger log = LoggerFactory.getLogger(ApprovalConfig.class);

    @Bean
    ApprovalChannel approvalChannel(PlatformProperties props) {
        var token = props.slackBotToken();
        if (token == null) {
            log.info("SLACK_BOT_TOKEN not set: approval requests are logged, not posted");
            return new InMemoryApprovalChannel();
        }
        var channel = props.slackApprovalChannel();
        if (channel == null) {
            throw new IllegalStateException("SLACK_APPROVAL_CHANNEL must be set when SLACK_BOT_TOKEN is set");
        }
        return new SlackApprovalChannel(props.slackApiUrl(), token, channel);
    }
}
