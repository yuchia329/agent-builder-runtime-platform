# Control plane image for the local stack (docker-compose.yml). In AWS the same jar runs on Lambda
# (ADR 0007). Build context: repo root, because the jar bundles files from contracts/.
#   docker build -f control-plane/Dockerfile .

FROM eclipse-temurin:21.0.9_10-jdk AS build
WORKDIR /src
COPY contracts/ contracts/
COPY control-plane/ control-plane/
WORKDIR /src/control-plane
# Cache Gradle's downloads between builds; tests run outside the image (they need Docker).
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon bootJar \
    && cp "$(find build/libs -name '*.jar' ! -name '*-plain.jar')" /app.jar

FROM eclipse-temurin:21.0.9_10-jre
RUN useradd --system --uid 10001 app
COPY --from=build /app.jar /app/app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
