package dev.agentplatform.controlplane.run;

import java.util.List;

/** A request body breaks its contract. Maps to 400 with every error listed. */
public class InvalidRequestException extends RuntimeException {

    private final List<String> errors;

    public InvalidRequestException(List<String> errors) {
        super("Invalid request: " + String.join("; ", errors));
        this.errors = List.copyOf(errors);
    }

    public List<String> errors() {
        return errors;
    }
}
