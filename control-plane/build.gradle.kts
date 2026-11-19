plugins {
    java
    id("org.springframework.boot") version "4.1.2"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "dev.agentplatform"
version = "0.0.1"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

dependencyManagement {
    imports {
        mavenBom("software.amazon.awssdk:bom:2.59.2")
    }
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("tools.jackson.dataformat:jackson-dataformat-yaml")
    implementation("com.networknt:json-schema-validator:3.0.9")
    implementation("com.nimbusds:nimbus-jose-jwt:10.3")
    implementation("software.amazon.awssdk:dynamodb")
    implementation("software.amazon.awssdk:sqs")
    // Lambda entry (ADR 0017): API Gateway HTTP API events into the same Spring MVC app.
    implementation("com.amazonaws.serverless:aws-serverless-java-container-springboot4:3.0.5")
    implementation("org.crac:crac:1.5.0")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.testcontainers:testcontainers")
    testImplementation("org.testcontainers:testcontainers-junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// Contracts live in contracts/; bundle them so the jar validates against the same files.
tasks.processResources {
    from("../contracts") {
        include("agent-spec.schema.json", "run-message.schema.json", "run-token.schema.json",
                "internal-api.openapi.yaml")
        into("contracts")
    }
}

tasks.test {
    useJUnitPlatform()
    // No background sweep during tests; RunSweepScheduleIntegrationTest turns it on itself.
    environment("SWEEP_INTERVAL_SECONDS", "0")
    // Test servers listen on 127.0.0.1 and clients call 127.0.0.1, never "localhost": a server on
    // all addresses can get a random port that another process (a Gradle daemon) holds on ::1, and
    // "localhost" may resolve to ::1, so requests would reach that process.
    environment("SERVER_ADDRESS", "127.0.0.1")
}

// `./gradlew bootRun` uses the local profile unless SPRING_PROFILES_ACTIVE says otherwise.
tasks.bootRun {
    environment("SPRING_PROFILES_ACTIVE", System.getenv("SPRING_PROFILES_ACTIVE") ?: "local")
}

// Lambda package (ADR 0017): classes and resources at the root, every runtime jar in lib/. Lambda
// cannot read Spring Boot's nested-jar layout, and a plain zip starts faster than a shaded jar.
// Handler: dev.agentplatform.controlplane.lambda.LambdaHandler::handleRequest
val lambdaZip by tasks.registering(Zip::class) {
    group = "build"
    description = "Builds the AWS Lambda package in build/distributions."
    archiveFileName = "control-plane-lambda.zip"
    from(tasks.compileJava)
    from(tasks.processResources)
    into("lib") {
        from(configurations.runtimeClasspath)
    }
}
