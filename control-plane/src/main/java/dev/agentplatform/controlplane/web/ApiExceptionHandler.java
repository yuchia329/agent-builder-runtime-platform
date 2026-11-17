package dev.agentplatform.controlplane.web;

import dev.agentplatform.controlplane.agent.InvalidSpecException;
import dev.agentplatform.controlplane.store.AgentAlreadyExistsException;
import dev.agentplatform.controlplane.store.AgentNotFoundException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/** Maps domain errors to RFC 9457 problem responses. */
@RestControllerAdvice
public class ApiExceptionHandler extends ResponseEntityExceptionHandler {

    @ExceptionHandler
    ProblemDetail invalidSpec(InvalidSpecException e) {
        var problem = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "The agent spec is invalid");
        problem.setTitle("Invalid agent spec");
        problem.setProperty("errors", e.errors());
        return problem;
    }

    @ExceptionHandler
    ProblemDetail notFound(AgentNotFoundException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler
    ProblemDetail conflict(AgentAlreadyExistsException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, e.getMessage());
    }
}
