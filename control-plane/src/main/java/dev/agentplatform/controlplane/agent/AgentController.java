package dev.agentplatform.controlplane.agent;

import dev.agentplatform.controlplane.tenant.ApiKeyAuthFilter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.net.URI;
import java.time.Instant;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/agents")
public class AgentController {

    /** Same rule as {@code name} in contracts/agent-spec.schema.json. */
    static final String NAME_PATTERN = "^[a-z][a-z0-9-]{1,62}$";

    private final AgentService service;

    public AgentController(AgentService service) {
        this.service = service;
    }

    public record CreateAgentRequest(@NotNull @Pattern(regexp = NAME_PATTERN) String name) {}

    public record AgentResponse(String name, int latestVersion, Integer activeVersion, String trigger,
                                Instant createdAt) {
        static AgentResponse of(Agent agent) {
            return new AgentResponse(agent.name(), agent.latestVersion(), agent.activeVersion(),
                    agent.trigger(), agent.createdAt());
        }
    }

    public record VersionResponse(String agent, int version, String trigger, boolean active, Instant createdAt) {}

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<AgentResponse> create(
            @RequestAttribute(ApiKeyAuthFilter.TENANT_ATTRIBUTE) String tenantId,
            @Valid @RequestBody CreateAgentRequest request) {
        var agent = service.createAgent(tenantId, request.name());
        return ResponseEntity.created(URI.create("/agents/" + agent.name())).body(AgentResponse.of(agent));
    }

    @PostMapping(path = "/{name}/versions",
            consumes = {"application/yaml", "application/x-yaml", "text/yaml", MediaType.APPLICATION_JSON_VALUE})
    public ResponseEntity<VersionResponse> publish(
            @RequestAttribute(ApiKeyAuthFilter.TENANT_ATTRIBUTE) String tenantId,
            @PathVariable String name,
            @RequestHeader(HttpHeaders.CONTENT_TYPE) MediaType contentType,
            @RequestBody String body) {
        var version = service.publishVersion(tenantId, name, body, contentType);
        return ResponseEntity.created(URI.create("/agents/%s/versions/%d".formatted(name, version.version())))
                .body(new VersionResponse(name, version.version(), version.trigger(), true, version.createdAt()));
    }
}
