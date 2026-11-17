package dev.agentplatform.controlplane.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;

class SpecValidatorTest {

    static final Path EXAMPLES = Path.of("..", "contracts", "examples");
    static final MediaType YAML = MediaType.parseMediaType("application/yaml");

    private final SpecValidator validator = new SpecValidator();

    static String example(String file) throws Exception {
        return Files.readString(EXAMPLES.resolve(file));
    }

    @Test
    void validExampleSpecPasses() throws Exception {
        var spec = validator.parseAndValidate(example("agent-spec.valid.yaml"), YAML);

        assertThat(spec.path("name").asString()).isEqualTo("issue-triage");
    }

    @Test
    void invalidExampleSpecListsEveryError() throws Exception {
        var body = example("agent-spec.invalid.yaml");

        assertThatThrownBy(() -> validator.parseAndValidate(body, YAML))
                .isInstanceOfSatisfying(InvalidSpecException.class, e -> assertThat(e.errors())
                        .hasSizeGreaterThanOrEqualTo(3)
                        .anySatisfy(err -> assertThat(err).startsWith("$: ").contains("'version'"))
                        .anySatisfy(err -> assertThat(err).startsWith("$.acts_as: "))
                        .anySatisfy(err -> assertThat(err).startsWith("$: ").contains("'budget'")));
    }

    @Test
    void approvalToolMustBeListedInTools() throws Exception {
        var body = example("agent-spec.valid.yaml")
                .replace("require_approval: [github.close_issue]", "require_approval: [github.delete_repo]");

        assertThatThrownBy(() -> validator.parseAndValidate(body, YAML))
                .isInstanceOfSatisfying(InvalidSpecException.class, e -> assertThat(e.errors())
                        .containsExactly("$.require_approval: 'github.delete_repo' is not listed in tools"));
    }

    @Test
    void acceptsJson() {
        var body = """
                {"name": "triage", "model": "m", "instructions": "i", "trigger": "github.issue.opened",
                 "acts_as": "owner", "tools": ["github.add_label"],
                 "budget": {"max_steps": 5, "max_usd": 0.5, "max_seconds": 60}}
                """;

        assertThat(validator.parseAndValidate(body, MediaType.APPLICATION_JSON).path("acts_as").asString())
                .isEqualTo("owner");
    }

    @Test
    void rejectsMalformedYaml() {
        assertThatThrownBy(() -> validator.parseAndValidate("name: [unclosed", YAML))
                .isInstanceOfSatisfying(InvalidSpecException.class,
                        e -> assertThat(e.errors().getFirst()).startsWith("$: not valid YAML"));
    }

    @Test
    void rejectsEmptyBody() {
        assertThatThrownBy(() -> validator.parseAndValidate("  ", YAML))
                .isInstanceOf(InvalidSpecException.class);
    }
}
