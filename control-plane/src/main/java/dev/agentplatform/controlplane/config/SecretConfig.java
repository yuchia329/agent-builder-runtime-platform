package dev.agentplatform.controlplane.config;

import dev.agentplatform.controlplane.secret.EnvSecretSource;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.secret.SsmSecretSource;
import java.util.EnumMap;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.ssm.SsmClient;

/**
 * Picks the {@link SecretSource} ({@code SECRETS_SOURCE}, ADR 0018) and makes the SSM and KMS
 * clients. Like the store clients, they read region and credentials from the environment and open
 * no connection until first used (Lambda SnapStart).
 */
@Configuration(proxyBeanMethods = false)
public class SecretConfig {

    @Bean
    @Lazy
    SsmClient ssmClient() {
        return SsmClient.create();
    }

    @Bean
    @Lazy
    KmsClient kmsClient() {
        return KmsClient.create();
    }

    @Bean
    SecretSource secretSource(PlatformProperties props, ObjectProvider<SsmClient> ssm) {
        return switch (props.secretsSource()) {
            case SSM -> new SsmSecretSource(ssm.getObject(), props.secretsSsmPath());
            case ENV -> {
                var values = new EnumMap<Secret, String>(Secret.class);
                values.put(Secret.TENANT_API_KEY, props.tenant().apiKey());
                values.put(Secret.GITHUB_WEBHOOK_SECRET, props.githubWebhookSecret());
                values.put(Secret.SLACK_BOT_TOKEN, props.slackBotToken());
                values.put(Secret.SLACK_SIGNING_SECRET, props.slackSigningSecret());
                yield new EnvSecretSource(values);
            }
        };
    }
}
