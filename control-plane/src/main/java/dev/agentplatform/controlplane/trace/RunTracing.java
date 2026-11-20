package dev.agentplatform.controlplane.trace;

import dev.agentplatform.controlplane.run.Run;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanBuilder;
import io.opentelemetry.api.trace.SpanContext;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.propagation.TextMapGetter;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

/**
 * Spans of a run's trace (ADR 0022). A run's trace starts when the run is created: its "run.start"
 * span has no parent, and its W3C {@code traceparent} is stored on the run and sent with every
 * queue message, so each worker attempt continues the same trace. Later control-plane work on the
 * run (an approval decision, a re-enqueue) adds a child of that span. The request that caused it
 * is linked, not the parent: one webhook can start several runs, each with its own trace.
 *
 * <p>Attributes name the tenant, agent, version and run; never input, output, tokens or keys.
 */
@Component
public class RunTracing {

    public static final AttributeKey<String> TENANT = AttributeKey.stringKey("tenant.id");
    public static final AttributeKey<String> AGENT = AttributeKey.stringKey("agent.name");
    public static final AttributeKey<Long> VERSION = AttributeKey.longKey("agent.version");
    public static final AttributeKey<String> RUN = AttributeKey.stringKey("run.id");

    private static final TextMapGetter<Map<String, String>> MAP_GETTER = new TextMapGetter<>() {
        @Override
        public Iterable<String> keys(Map<String, String> carrier) {
            return carrier.keySet();
        }

        @Override
        public String get(Map<String, String> carrier, String key) {
            return carrier == null ? null : carrier.get(key);
        }
    };

    private final Tracer tracer;

    public RunTracing(Tracer tracer) {
        this.tracer = tracer;
    }

    /** Makes no spans and no ids (for tests that do not look at tracing). */
    public static RunTracing noop() {
        return new RunTracing(OpenTelemetry.noop().getTracer(TracingConfig.SCOPE));
    }

    /** Starts the span that opens a new run's trace. The caller ends it ({@link #inSpan}). */
    public Span startRun(String runId, String tenantId, String agentName, int agentVersion) {
        var builder = tracer.spanBuilder("run.start").setNoParent()
                .setAttribute(RUN, runId)
                .setAttribute(TENANT, tenantId)
                .setAttribute(AGENT, agentName)
                .setAttribute(VERSION, (long) agentVersion);
        return linkCurrent(builder).startSpan();
    }

    /**
     * Starts a span named {@code name} in {@code run}'s trace (a child of its "run.start" span), or
     * a new trace if the run has none (created before tracing). The caller ends it.
     */
    public Span startInRun(Run run, String name) {
        var builder = tracer.spanBuilder(name)
                .setAttribute(RUN, run.runId())
                .setAttribute(TENANT, run.tenantId())
                .setAttribute(AGENT, run.agentName())
                .setAttribute(VERSION, (long) run.agentVersion());
        var parent = parse(run.traceParent());
        if (parent.isValid()) {
            builder.setParent(Context.root().with(Span.wrap(parent)));
        } else {
            builder.setNoParent();
        }
        return linkCurrent(builder).startSpan();
    }

    /** Starts a span with no parent (scheduled work). The caller ends it. */
    public Span startRoot(String name) {
        return tracer.spanBuilder(name).setNoParent().startSpan();
    }

    /**
     * Runs {@code body} with {@code span} current (so AWS SDK calls become its children), marks the
     * span failed if it throws (exception type only: messages can carry data), then ends it.
     */
    public static <T> T inSpan(Span span, Supplier<T> body) {
        try (var ignored = span.makeCurrent()) {
            return body.get();
        } catch (RuntimeException e) {
            span.setStatus(StatusCode.ERROR, e.getClass().getSimpleName());
            throw e;
        } finally {
            span.end();
        }
    }

    /** The W3C {@code traceparent} of {@code span}, or null if it is not recording a real trace. */
    public static String traceParent(Span span) {
        var context = span.getSpanContext();
        if (!context.isValid()) {
            return null;
        }
        return "00-" + context.getTraceId() + "-" + context.getSpanId() + "-" + context.getTraceFlags().asHex();
    }

    /** The span context in a W3C {@code traceparent}; invalid if null or malformed. */
    public static SpanContext parse(String traceParent) {
        if (traceParent == null) {
            return SpanContext.getInvalid();
        }
        var context = W3CTraceContextPropagator.getInstance()
                .extract(Context.root(), Map.of("traceparent", traceParent), MAP_GETTER);
        return Span.fromContext(context).getSpanContext();
    }

    private static SpanBuilder linkCurrent(SpanBuilder builder) {
        var current = Span.current().getSpanContext();
        if (current.isValid()) {
            builder.addLink(current);
        }
        return builder;
    }
}
