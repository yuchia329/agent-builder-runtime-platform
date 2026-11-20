package dev.agentplatform.controlplane.trace;

import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.util.List;

/** A real tracer whose finished spans stay in memory, for unit tests. */
public final class TestTracing {

    public final InMemorySpanExporter exporter = InMemorySpanExporter.create();
    public final RunTracing tracing;

    public TestTracing() {
        var provider = SdkTracerProvider.builder()
                .setIdGenerator(new SecureIdGenerator())
                .addSpanProcessor(SimpleSpanProcessor.create(exporter))
                .build();
        tracing = new RunTracing(provider.get(TracingConfig.SCOPE));
    }

    public List<SpanData> spans() {
        return exporter.getFinishedSpanItems();
    }

    /** The one finished span named {@code name}. */
    public SpanData span(String name) {
        var found = spans().stream().filter(s -> s.getName().equals(name)).toList();
        if (found.size() != 1) {
            throw new AssertionError("Expected one span '" + name + "', found " + spans());
        }
        return found.getFirst();
    }
}
