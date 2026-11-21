package dev.agentplatform.controlplane.internal;

import dev.agentplatform.controlplane.contract.ContractValidator;
import dev.agentplatform.controlplane.run.InvalidRequestException;
import dev.agentplatform.controlplane.run.Run;
import dev.agentplatform.controlplane.run.RunService;
import dev.agentplatform.controlplane.run.RunStatus;
import dev.agentplatform.controlplane.run.RunUpdate;
import dev.agentplatform.controlplane.run.RunViews;
import dev.agentplatform.controlplane.token.RunTokenIssuer;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Internal run API for workers (contracts/internal-api.openapi.yaml). Not behind the API key: in
 * AWS, API Gateway requires a SigV4 signature on /internal/* (ADR 0007).
 */
@RestController
@RequestMapping("/internal/runs")
public class InternalRunController {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private final RunService service;
    private final ContractValidator contracts;
    private final RunTokenIssuer tokens;

    public InternalRunController(RunService service, ContractValidator contracts, RunTokenIssuer tokens) {
        this.service = service;
        this.contracts = contracts;
        this.tokens = tokens;
    }

    /** The run, its spec and input, and a new run token for this attempt (when tokens are on). */
    @GetMapping("/{runId}")
    public RunViews.Internal get(@PathVariable String runId) {
        var loaded = service.loadForWorker(runId);
        return RunViews.internal(loaded, tokens.issue(loaded));
    }

    @PatchMapping(path = "/{runId}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public RunViews.Summary update(@PathVariable String runId, @RequestBody JsonNode body) {
        var errors = contracts.validate(ContractValidator.RUN_UPDATE, body);
        if (!errors.isEmpty()) {
            throw new InvalidRequestException(errors);
        }
        return RunViews.summary(service.update(runId, toUpdate(body)));
    }

    /** Sets a RUNNING run aside and re-queues it after a delay (ADR 0027). */
    @PostMapping(path = "/{runId}/defer", consumes = MediaType.APPLICATION_JSON_VALUE)
    public RunViews.Summary defer(@PathVariable String runId, @RequestBody JsonNode body) {
        var errors = contracts.validate(ContractValidator.RUN_DEFER, body);
        if (!errors.isEmpty()) {
            throw new InvalidRequestException(errors);
        }
        return RunViews.summary(service.defer(runId, body.get("retry_after_seconds").asInt()));
    }

    /** Body has already passed the RunUpdate contract. */
    private static RunUpdate toUpdate(JsonNode body) {
        var failure = body.get("failure");
        var pending = body.get("pending_approval");
        return new RunUpdate(
                RunStatus.valueOf(body.get("status").asString()),
                body.has("steps") ? body.get("steps").asInt() : null,
                body.has("cost_usd") ? body.get("cost_usd").decimalValue() : null,
                body.has("trace_id") ? body.get("trace_id").asString() : null,
                pending == null ? null : JSON.writeValueAsString(pending),
                failure == null ? null : new Run.Failure(failure.get("reason").asString(), failure.get("message").asString()),
                body.has("output") ? body.get("output").asString() : null);
    }
}
