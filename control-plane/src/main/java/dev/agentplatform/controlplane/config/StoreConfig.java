package dev.agentplatform.controlplane.config;

import dev.agentplatform.controlplane.store.AgentStore;
import dev.agentplatform.controlplane.store.DynamoDbAgentStore;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import java.time.Clock;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * Picks the {@link AgentStore} adapter from {@code platform.store-backend}. The DynamoDB client
 * reads region, credentials and endpoint (AWS_ENDPOINT_URL_DYNAMODB locally) from the
 * environment and opens no connection at startup, which keeps Lambda SnapStart cheap.
 */
@Configuration(proxyBeanMethods = false)
public class StoreConfig {

    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }

    @Bean
    @Lazy
    DynamoDbClient dynamoDbClient() {
        return DynamoDbClient.create();
    }

    @Bean
    AgentStore agentStore(PlatformProperties props, Clock clock,
                          ObjectProvider<DynamoDbClient> dynamo) {
        return switch (props.storeBackend()) {
            case MEMORY -> new InMemoryAgentStore(clock);
            case DYNAMODB -> new DynamoDbAgentStore(dynamo.getObject(), props.table(), clock);
        };
    }
}
