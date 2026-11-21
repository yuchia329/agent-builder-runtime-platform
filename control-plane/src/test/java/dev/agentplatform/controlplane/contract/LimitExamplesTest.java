package dev.agentplatform.controlplane.contract;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/** The contract's tenant-limit examples pass the control plane's own checks (M8). */
class LimitExamplesTest {

    private static final Path EXAMPLES = Path.of("..", "contracts", "examples");
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final ContractValidator contracts = new ContractValidator();

    private java.util.List<String> errors(String contract, String file) throws Exception {
        return contracts.validate(contract, JSON.readTree(Files.readString(EXAMPLES.resolve(file))));
    }

    @Test
    void deferExamples() throws Exception {
        assertThat(errors(ContractValidator.RUN_DEFER, "internal-run-defer.valid.json")).isEmpty();
        assertThat(errors(ContractValidator.RUN_DEFER, "internal-run-defer.invalid.json")).isNotEmpty();
    }

    @Test
    void deferredProblemExample() throws Exception {
        assertThat(errors(ContractValidator.DEFERRED_PROBLEM, "deferred-problem.valid.json")).isEmpty();
    }
}
