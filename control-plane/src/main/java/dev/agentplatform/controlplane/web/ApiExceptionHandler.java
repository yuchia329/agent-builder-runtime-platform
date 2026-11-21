package dev.agentplatform.controlplane.web;

import dev.agentplatform.controlplane.agent.InvalidSpecException;
import dev.agentplatform.controlplane.queue.RunQueueException;
import dev.agentplatform.controlplane.secret.SecretUnavailableException;
import dev.agentplatform.controlplane.run.AgentNotPublishedException;
import dev.agentplatform.controlplane.run.InvalidRequestException;
import dev.agentplatform.controlplane.run.RunDeferredException;
import dev.agentplatform.controlplane.run.TenantBudgetExceededException;
import dev.agentplatform.controlplane.store.AgentAlreadyExistsException;
import dev.agentplatform.controlplane.store.ApprovalConflictException;
import dev.agentplatform.controlplane.store.AgentNotFoundException;
import dev.agentplatform.controlplane.store.IllegalTransitionException;
import dev.agentplatform.controlplane.store.RunNotFoundException;
import dev.agentplatform.controlplane.trigger.WebhookRejectedException;
import java.net.URI;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/** Maps domain errors to RFC 9457 problem responses. */
@RestControllerAdvice
public class ApiExceptionHandler extends ResponseEntityExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler
    ProblemDetail invalidSpec(InvalidSpecException e) {
        return withErrors("Invalid agent spec", "The agent spec is invalid", e.errors());
    }

    @ExceptionHandler
    ProblemDetail invalidRequest(InvalidRequestException e) {
        return withErrors("Invalid request", "The request body is invalid", e.errors());
    }

    @ExceptionHandler({AgentNotFoundException.class, RunNotFoundException.class})
    ProblemDetail notFound(RuntimeException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler({AgentAlreadyExistsException.class, AgentNotPublishedException.class,
            IllegalTransitionException.class, ApprovalConflictException.class})
    ProblemDetail conflict(RuntimeException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, e.getMessage());
    }

    /** 429 to a worker's start at the tenant's cap: the Deferred problem of the internal API. */
    @ExceptionHandler
    ResponseEntity<ProblemDetail> deferred(RunDeferredException e) {
        return tooManyRequests("urn:agent-platform:problem:deferred", "Start deferred", e.getMessage(),
                RunDeferredException.REASON, e.retryAfterSeconds());
    }

    /** 429 to {@code POST /runs} (and webhooks) once the tenant reached its daily spend cap. */
    @ExceptionHandler
    ResponseEntity<ProblemDetail> overDailyCap(TenantBudgetExceededException e) {
        return tooManyRequests("urn:agent-platform:problem:tenant-budget", "Daily spend cap reached", e.getMessage(),
                TenantBudgetExceededException.REASON, e.retryAfterSeconds());
    }

    private static ResponseEntity<ProblemDetail> tooManyRequests(String type, String title, String detail,
                                                                 String reason, int retryAfterSeconds) {
        var problem = ProblemDetail.forStatusAndDetail(HttpStatus.TOO_MANY_REQUESTS, detail);
        problem.setType(URI.create(type));
        problem.setTitle(title);
        problem.setProperty("reason", reason);
        problem.setProperty("retry_after_seconds", retryAfterSeconds);
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, Integer.toString(retryAfterSeconds))
                .body(problem);
    }

    @ExceptionHandler
    ProblemDetail queueUnavailable(RunQueueException e) {
        log.error("Run saved as QUEUED but not enqueued", e);
        return ProblemDetail.forStatusAndDetail(HttpStatus.SERVICE_UNAVAILABLE,
                e.getMessage() + "; the run stays QUEUED and will be re-enqueued");
    }

    @ExceptionHandler
    ProblemDetail secretsUnavailable(SecretUnavailableException e) {
        log.warn("Secrets unavailable: {}", e.getMessage());
        return ProblemDetail.forStatusAndDetail(HttpStatus.SERVICE_UNAVAILABLE, "Try again shortly");
    }

    @ExceptionHandler
    ProblemDetail webhookRejected(WebhookRejectedException e) {
        return ProblemDetail.forStatusAndDetail(e.status(), e.getMessage());
    }

    private static ProblemDetail withErrors(String title, String detail, List<String> errors) {
        var problem = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, detail);
        problem.setTitle(title);
        problem.setProperty("errors", errors);
        return problem;
    }
}
