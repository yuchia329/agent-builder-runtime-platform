package dev.agentplatform.controlplane.store;

import java.net.URI;
import java.time.Clock;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
@Testcontainers
class DynamoDbAgentStoreContractTest extends AgentStoreContractTest {

    @Container
    static final GenericContainer<?> DYNAMODB = new GenericContainer<>("amazon/dynamodb-local:3.3.2")
            .withCommand("-jar", "DynamoDBLocal.jar", "-inMemory", "-sharedDb")
            .withExposedPorts(8000);

    private static DynamoDbClient client;

    @BeforeAll
    static void connect() {
        client = DynamoDbClient.builder()
                .endpointOverride(URI.create("http://%s:%d".formatted(DYNAMODB.getHost(), DYNAMODB.getMappedPort(8000))))
                .region(Region.US_EAST_1)
                .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("local", "local")))
                .build();
    }

    @AfterAll
    static void close() {
        client.close();
    }

    @Override
    protected AgentStore newStore() {
        // A fresh table per test, created from the same definition local-stack and infra use.
        var table = "control-plane-" + UUID.randomUUID();
        ControlPlaneTable.create(client, table);
        return new DynamoDbAgentStore(client, table, Clock.systemUTC());
    }
}
