package dev.agentplatform.controlplane.store;

import java.net.URI;
import java.util.UUID;
import org.testcontainers.containers.GenericContainer;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/** One DynamoDB Local container shared by all tests in the JVM. Needs Docker. */
final class DynamoDbLocal {

    private static final GenericContainer<?> CONTAINER = new GenericContainer<>("amazon/dynamodb-local:3.3.2")
            .withCommand("-jar", "DynamoDBLocal.jar", "-inMemory", "-sharedDb")
            .withExposedPorts(8000);

    private static DynamoDbClient client;

    private DynamoDbLocal() {}

    static synchronized DynamoDbClient client() {
        if (client == null) {
            CONTAINER.start(); // stopped by Testcontainers' cleanup when the JVM exits
            client = DynamoDbClient.builder()
                    .endpointOverride(URI.create("http://%s:%d".formatted(CONTAINER.getHost(), CONTAINER.getMappedPort(8000))))
                    .region(Region.US_EAST_1)
                    .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("local", "local")))
                    .build();
        }
        return client;
    }

    /** A fresh, empty control-plane table; returns its name. */
    static String newTable() {
        var table = "control-plane-" + UUID.randomUUID();
        ControlPlaneTable.create(client(), table);
        return table;
    }
}
