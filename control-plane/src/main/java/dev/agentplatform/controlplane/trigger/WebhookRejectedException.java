package dev.agentplatform.controlplane.trigger;

import org.springframework.http.HttpStatus;

/** A webhook delivery refused before any run starts; {@code status} is the HTTP answer. */
public class WebhookRejectedException extends RuntimeException {

    private final HttpStatus status;

    public WebhookRejectedException(HttpStatus status, String message) {
        super(message);
        this.status = status;
    }

    public HttpStatus status() {
        return status;
    }
}
