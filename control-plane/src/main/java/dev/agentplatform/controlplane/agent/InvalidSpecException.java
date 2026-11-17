package dev.agentplatform.controlplane.agent;

import java.util.List;

/** The published spec is malformed or breaks a spec rule. Maps to 400 with every error listed. */
public class InvalidSpecException extends RuntimeException {

    private final List<String> errors;

    public InvalidSpecException(List<String> errors) {
        super("Invalid agent spec: " + String.join("; ", errors));
        this.errors = List.copyOf(errors);
    }

    public List<String> errors() {
        return errors;
    }
}
