package dev.agentplatform.controlplane.agent;

import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.dataformat.yaml.YAMLMapper;

/**
 * Parses a spec (YAML or JSON) and checks it against {@code contracts/agent-spec.schema.json}
 * (bundled into the jar at build time) plus the rules JSON Schema cannot express.
 */
@Component
public class SpecValidator {

    static final String SCHEMA_RESOURCE = "/contracts/agent-spec.schema.json";

    private final Schema schema;
    private final JsonMapper json = JsonMapper.builder().build();
    private final YAMLMapper yaml = YAMLMapper.builder().build();

    public SpecValidator() {
        try (var in = SpecValidator.class.getResourceAsStream(SCHEMA_RESOURCE)) {
            if (in == null) {
                throw new IllegalStateException("Missing " + SCHEMA_RESOURCE + " on the classpath");
            }
            schema = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12).getSchema(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Parses and validates; returns the spec as a JSON tree. */
    public JsonNode parseAndValidate(String body, MediaType contentType) {
        var spec = parse(body, contentType);
        validate(spec);
        return spec;
    }

    public String toJson(JsonNode spec) {
        return json.writeValueAsString(spec);
    }

    JsonNode parse(String body, MediaType contentType) {
        if (body == null || body.isBlank()) {
            throw new InvalidSpecException(List.of("$: spec is empty"));
        }
        try {
            var mapper = contentType != null && contentType.getSubtype().contains("yaml") ? yaml : json;
            var tree = mapper.readTree(body);
            if (tree == null || tree.isMissingNode()) {
                throw new InvalidSpecException(List.of("$: spec is empty"));
            }
            return tree;
        } catch (JacksonException e) {
            throw new InvalidSpecException(List.of("$: not valid " + describe(contentType) + ": "
                    + e.getOriginalMessage()));
        }
    }

    void validate(JsonNode spec) {
        var errors = new ArrayList<String>();
        for (var error : schema.validate(spec)) {
            errors.add(path(error.getInstanceLocation().toString()) + ": " + error.getMessage());
        }
        if (errors.isEmpty()) {
            errors.addAll(approvalsSubsetOfTools(spec));
        }
        if (!errors.isEmpty()) {
            throw new InvalidSpecException(errors);
        }
    }

    /** Every tool in require_approval must also be in tools (not expressible in the schema). */
    private static List<String> approvalsSubsetOfTools(JsonNode spec) {
        var tools = new HashSet<String>();
        spec.path("tools").forEach(tool -> tools.add(tool.asString()));
        var errors = new ArrayList<String>();
        spec.path("require_approval").forEach(tool -> {
            if (!tools.contains(tool.asString())) {
                errors.add("$.require_approval: '%s' is not listed in tools".formatted(tool.asString()));
            }
        });
        return errors;
    }

    /** JSON Pointer ("", "/budget/max_usd") to the "$.budget.max_usd" form used in all errors. */
    private static String path(String pointer) {
        return pointer.isEmpty() || pointer.equals("/") ? "$" : "$" + pointer.replace('/', '.');
    }

    private static String describe(MediaType contentType) {
        return contentType != null && contentType.getSubtype().contains("yaml") ? "YAML" : "JSON";
    }
}
