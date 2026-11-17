# control-plane

Java 21, Spring Boot 4, Gradle (Kotlin DSL). Design: `docs/architecture.md` → Control plane.

## Commands

Run from `control-plane/`.

| What | Command |
| --- | --- |
| Test (all) | `./gradlew test` (Docker must be running for the DynamoDB suite) |
| Test without Docker | `./gradlew test --tests '*InMemory*' --tests '*SpecValidator*' --tests '*AgentApi*'` |
| Run locally | `./gradlew bootRun` (profile `local`, in-memory store, API key `local-dev-key`, port 8080) |
| Build jar | `./gradlew bootJar` |

Try it:

```sh
curl -X POST localhost:8080/agents -H 'X-Api-Key: local-dev-key' \
  -H 'Content-Type: application/json' -d '{"name":"issue-triage"}'
curl -X POST localhost:8080/agents/issue-triage/versions -H 'X-Api-Key: local-dev-key' \
  -H 'Content-Type: application/yaml' --data-binary @../contracts/examples/agent-spec.valid.yaml
```

## JDK

No system JDK 21 needed. `gradle/gradle-daemon-jvm.properties` makes Gradle download Temurin 21
into `~/.gradle/jdks` and run the build on it; the `./gradlew` launcher works on any Java 8+.
Keep Gradle on 8.14.x while machines may only have Java 11 (Gradle 9 needs Java 17 to launch).

## Conventions

- Package `dev.agentplatform.controlplane`, one package per area (`agent`, `store`, `tenant`,
  `config`, `web`).
- Ports live in `store/` (`AgentStore`, later `RunStore`). Each port has an abstract
  `*ContractTest`; every adapter has a subclass of it. New adapters must pass it.
- DynamoDB adapters use the plain `DynamoDbClient` (not the Enhanced Client). Key helpers and the
  item layout are documented at the top of each adapter.
- The table's keys and indexes live only in `dynamodb/control-plane-table.json`. Tests create
  tables from it (`ControlPlaneTable`); change the table there, never in code.
- The agent spec schema is copied from `../contracts/` into the jar at build time; never keep a
  second copy.
- Jackson 3 (`tools.jackson.*`), not Jackson 2 (`com.fasterxml.jackson.*`).
- Config comes from env vars via `application.yml` (`STORE_BACKEND`, `CONTROL_PLANE_TABLE`,
  `TENANT_ID`, `TENANT_API_KEY`, `AWS_ENDPOINT_URL_DYNAMODB`). No network calls at startup
  (Lambda SnapStart).
- Errors: throw domain exceptions; `web/ApiExceptionHandler` maps them to problem+json.
