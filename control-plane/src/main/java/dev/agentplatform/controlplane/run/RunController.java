package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.tenant.ApiKeyAuthFilter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.net.URI;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Public run endpoints: start a run by hand, read its status. */
@RestController
@RequestMapping("/runs")
public class RunController {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final RunService service;

    public RunController(RunService service) {
        this.service = service;
    }

    /** {@code input} is free-form and defaults to an empty object. */
    public record StartRunRequest(@NotBlank String agent, JsonNode input) {}

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<RunViews.Summary> start(
            @RequestAttribute(ApiKeyAuthFilter.TENANT_ATTRIBUTE) String tenantId,
            @Valid @RequestBody StartRunRequest request) {
        var input = request.input() == null || request.input().isNull() ? JSON.createObjectNode() : request.input();
        if (!input.isObject()) {
            throw new InvalidRequestException(java.util.List.of("$.input: must be an object"));
        }
        var run = service.start(tenantId, request.agent(), JSON.writeValueAsString(input));
        return ResponseEntity.created(URI.create("/runs/" + run.runId())).body(RunViews.summary(run));
    }

    @GetMapping("/{runId}")
    public RunViews.Summary get(
            @RequestAttribute(ApiKeyAuthFilter.TENANT_ATTRIBUTE) String tenantId,
            @PathVariable String runId) {
        return RunViews.summary(service.get(tenantId, runId));
    }
}
