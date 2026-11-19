package dev.agentplatform.controlplane.contract;

import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaLocation;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * Validates JSON against the files in {@code contracts/} (bundled into the jar under
 * {@code contracts/}), so the control plane never keeps its own copy of a shape.
 */
@Component
public class ContractValidator {

    public static final String RUN_MESSAGE = "run-message.schema.json";
    public static final String RUN_TOKEN = "run-token.schema.json";
    public static final String INTERNAL_RUN = "internal-api.openapi.yaml#/components/schemas/InternalRun";
    public static final String RUN_SUMMARY = "internal-api.openapi.yaml#/components/schemas/RunSummary";
    public static final String RUN_UPDATE = "internal-api.openapi.yaml#/components/schemas/RunUpdate";

    private final SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
    private final Map<String, Schema> schemas = new ConcurrentHashMap<>();

    /** Returns every violation as "$.path: message"; empty when valid. */
    public List<String> validate(String contract, JsonNode json) {
        var schema = schemas.computeIfAbsent(contract,
                ref -> registry.getSchema(SchemaLocation.of("classpath:contracts/" + ref)));
        return schema.validate(json).stream().map(ContractValidator::format).toList();
    }

    public static String format(Error error) {
        var pointer = error.getInstanceLocation().toString();
        var path = pointer.isEmpty() || pointer.equals("/") ? "$" : "$" + pointer.replace('/', '.');
        return path + ": " + error.getMessage();
    }
}
