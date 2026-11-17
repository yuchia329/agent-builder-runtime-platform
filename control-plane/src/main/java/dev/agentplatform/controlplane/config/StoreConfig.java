package dev.agentplatform.controlplane.config;

import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.queue.SqsRunQueuePublisher;
import dev.agentplatform.controlplane.store.AgentStore;
import dev.agentplatform.controlplane.store.DeliveryStore;
import dev.agentplatform.controlplane.store.DynamoDbDeliveryStore;
import dev.agentplatform.controlplane.store.DynamoDbAgentStore;
import dev.agentplatform.controlplane.store.DynamoDbRunStore;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import dev.agentplatform.controlplane.store.InMemoryDeliveryStore;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import dev.agentplatform.controlplane.store.RunStore;
import java.time.Clock;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.sqs.SqsClient;

/**
 * Picks adapters from {@code platform.store-backend} and {@code platform.queue-backend}. AWS
 * clients read region, credentials and endpoints (AWS_ENDPOINT_URL_DYNAMODB / _SQS locally) from
 * the environment and open no connection at startup, which keeps Lambda SnapStart cheap.
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
    @Lazy
    SqsClient sqsClient() {
        return SqsClient.create();
    }

    @Bean
    AgentStore agentStore(PlatformProperties props, Clock clock, ObjectProvider<DynamoDbClient> dynamo) {
        return switch (props.storeBackend()) {
            case MEMORY -> new InMemoryAgentStore(clock);
            case DYNAMODB -> new DynamoDbAgentStore(dynamo.getObject(), props.table(), clock);
        };
    }

    @Bean
    RunStore runStore(PlatformProperties props, ObjectProvider<DynamoDbClient> dynamo) {
        return switch (props.storeBackend()) {
            case MEMORY -> new InMemoryRunStore();
            case DYNAMODB -> new DynamoDbRunStore(dynamo.getObject(), props.table());
        };
    }

    @Bean
    DeliveryStore deliveryStore(PlatformProperties props, ObjectProvider<DynamoDbClient> dynamo) {
        return switch (props.storeBackend()) {
            case MEMORY -> new InMemoryDeliveryStore();
            case DYNAMODB -> new DynamoDbDeliveryStore(dynamo.getObject(), props.table());
        };
    }

    @Bean
    RunQueuePublisher runQueuePublisher(PlatformProperties props, ObjectProvider<SqsClient> sqs) {
        return switch (props.queueBackend()) {
            case MEMORY -> new InMemoryRunQueuePublisher();
            case SQS -> {
                if (props.runQueueUrl() == null || props.runQueueUrl().isBlank()) {
                    throw new IllegalStateException("RUN_QUEUE_URL must be set when QUEUE_BACKEND=sqs");
                }
                yield new SqsRunQueuePublisher(sqs.getObject(), props.runQueueUrl());
            }
        };
    }
}
