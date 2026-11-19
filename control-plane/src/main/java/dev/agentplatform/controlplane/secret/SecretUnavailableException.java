package dev.agentplatform.controlplane.secret;

/** The secret store could not be read; nothing is cached, so the next request tries again. */
public class SecretUnavailableException extends RuntimeException {

    public SecretUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
