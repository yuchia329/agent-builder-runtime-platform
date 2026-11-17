package dev.agentplatform.controlplane.queue;

public class RunQueueException extends RuntimeException {

    public RunQueueException(String message, Throwable cause) {
        super(message, cause);
    }
}
