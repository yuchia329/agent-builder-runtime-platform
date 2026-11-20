package dev.agentplatform.controlplane.trace;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.exporter.logging.LoggingSpanExporter;
import io.opentelemetry.instrumentation.awssdk.v2_2.AwsSdkTelemetry;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import io.opentelemetry.sdk.trace.samplers.Sampler;
import java.util.Locale;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.core.interceptor.ExecutionInterceptor;

/**
 * OpenTelemetry for the control plane (ADR 0022), set up by hand: no Java agent, nothing global.
 * {@code TRACING_EXPORTER}: {@code none} (default, and on Lambda: spans and trace ids are still
 * made, so each run's trace id reaches the worker, but nothing is uploaded) or {@code logging}
 * (each span in the log, for local checks).
 *
 * <p>Uploading from Lambda was measured and left out (ADR 0022): Lambda's X-Ray agent forwards
 * spans only for the requests it samples, none after a SnapStart restore, and active tracing adds
 * about 250 ms to a cold start. Every span is kept and exported as it ends (a frozen Lambda cannot
 * finish a batch later), so an exporter added here must be fast or run after the response.
 */
@Configuration(proxyBeanMethods = false)
public class TracingConfig {

    public enum Exporter { NONE, LOGGING }

    static final String SERVICE_NAME = "control-plane";
    static final String SCOPE = "dev.agentplatform.controlplane";

    @Bean
    TraceExport traceExport(@Value("${platform.tracing.exporter:none}") String exporter) {
        return switch (parse(exporter)) {
            case NONE -> new TraceExport(null);
            case LOGGING -> new TraceExport(LoggingSpanExporter::create);
        };
    }

    static Exporter parse(String value) {
        var name = value == null || value.isBlank() ? "none" : value.trim();
        try {
            return Exporter.valueOf(name.toUpperCase(Locale.ROOT).replace('-', '_'));
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException("TRACING_EXPORTER must be none or logging, not '" + name + "'");
        }
    }

    @Bean
    OpenTelemetrySdk openTelemetry(TraceExport export) {
        var resource = Resource.getDefault().merge(Resource.create(
                Attributes.of(AttributeKey.stringKey("service.name"), SERVICE_NAME)));
        var tracerProvider = SdkTracerProvider.builder()
                .setResource(resource)
                .setIdGenerator(new SecureIdGenerator())
                .setSampler(Sampler.alwaysOn())
                .addSpanProcessor(SimpleSpanProcessor.create(export))
                .build();
        return OpenTelemetrySdk.builder()
                .setTracerProvider(tracerProvider)
                .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
                .build();
    }

    @Bean
    Tracer tracer(OpenTelemetry openTelemetry) {
        return openTelemetry.getTracer(SCOPE);
    }

    /** Adds a span for every AWS SDK call (DynamoDB, SQS, KMS, SSM); added to each client. */
    @Bean
    ExecutionInterceptor awsSdkTracing(OpenTelemetry openTelemetry) {
        return AwsSdkTelemetry.builder(openTelemetry).build().createExecutionInterceptor();
    }
}
