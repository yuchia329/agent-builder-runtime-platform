package dev.agentplatform.controlplane.trace;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.propagation.TextMapGetter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Collections;
import java.util.Map;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

/**
 * One SERVER span per HTTP request, named {@code <METHOD> <route>} ({@code POST /runs},
 * {@code PATCH /internal/runs/{id}}). Only {@code /internal/*} requests (workers and the gateway,
 * IAM-signed on AWS) may continue a caller's trace through {@code traceparent}; a public caller
 * cannot attach spans to a trace it names. Attributes: method, route, status and the run id from
 * the route; never headers, query strings or bodies.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
public class TracingFilter extends OncePerRequestFilter {

    static final AttributeKey<String> METHOD = AttributeKey.stringKey("http.request.method");
    static final AttributeKey<String> ROUTE = AttributeKey.stringKey("http.route");
    static final AttributeKey<Long> STATUS = AttributeKey.longKey("http.response.status_code");

    private static final TextMapGetter<HttpServletRequest> HEADERS = new TextMapGetter<>() {
        @Override
        public Iterable<String> keys(HttpServletRequest request) {
            return Collections.list(request.getHeaderNames());
        }

        @Override
        public String get(HttpServletRequest request, String key) {
            return request == null ? null : request.getHeader(key);
        }
    };

    private final Tracer tracer;

    public TracingFilter(Tracer tracer) {
        this.tracer = tracer;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        var parent = request.getRequestURI().startsWith("/internal/")
                ? W3CTraceContextPropagator.getInstance().extract(Context.root(), request, HEADERS)
                : Context.root();
        var span = tracer.spanBuilder(request.getMethod())
                .setParent(parent)
                .setSpanKind(SpanKind.SERVER)
                .setAttribute(METHOD, request.getMethod())
                .startSpan();
        try (var ignored = span.makeCurrent()) {
            chain.doFilter(request, response);
        } catch (IOException | ServletException | RuntimeException e) {
            span.setStatus(StatusCode.ERROR, e.getClass().getSimpleName());
            throw e;
        } finally {
            if (request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE) instanceof String route) {
                span.updateName(request.getMethod() + " " + route);
                span.setAttribute(ROUTE, route);
                if (request.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE) instanceof Map<?, ?> vars
                        && vars.get("runId") instanceof String runId) {
                    span.setAttribute(RunTracing.RUN, runId);
                }
            }
            var status = response.getStatus();
            span.setAttribute(STATUS, (long) status);
            if (status >= 500) {
                span.setStatus(StatusCode.ERROR);
            }
            span.end();
        }
    }
}
