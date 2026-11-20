package dev.agentplatform.controlplane.trace;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import io.opentelemetry.api.trace.SpanId;
import io.opentelemetry.api.trace.TraceId;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.util.HashSet;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class TraceExportTest {

    @Test
    void idsAreValidAndDoNotRepeat() {
        var ids = new SecureIdGenerator();
        var seen = new HashSet<String>();
        for (int i = 0; i < 1000; i++) {
            var trace = ids.generateTraceId();
            var span = ids.generateSpanId();
            assertThat(TraceId.isValid(trace)).isTrue();
            assertThat(SpanId.isValid(span)).isTrue();
            assertThat(seen.add(trace)).isTrue();
        }
    }

    @Test
    void exporterSettingIsParsed() {
        assertThat(TracingConfig.parse(null)).isEqualTo(TracingConfig.Exporter.NONE);
        assertThat(TracingConfig.parse("")).isEqualTo(TracingConfig.Exporter.NONE);
        assertThat(TracingConfig.parse("logging")).isEqualTo(TracingConfig.Exporter.LOGGING);
        assertThatThrownBy(() -> TracingConfig.parse("xray-udp")).hasMessageContaining("TRACING_EXPORTER");
    }

    @Test
    void exporterIsMadeOnFirstSpanDroppedWhilePausedAndMadeAgainAfterTheSnapshot() {
        var made = new AtomicInteger();
        var exporters = new java.util.ArrayList<InMemorySpanExporter>();
        var export = new TraceExport(() -> {
            made.incrementAndGet();
            var memory = InMemorySpanExporter.create();
            exporters.add(memory);
            return memory;
        });
        var tracer = SdkTracerProvider.builder().addSpanProcessor(SimpleSpanProcessor.create(export)).build()
                .get("test");
        assertThat(made).hasValue(0);

        export.pause();
        tracer.spanBuilder("priming").startSpan().end();
        assertThat(made).hasValue(0);
        export.resume();

        tracer.spanBuilder("first").startSpan().end();
        assertThat(made).hasValue(1);
        assertThat(exporters.get(0).getFinishedSpanItems()).extracting(s -> s.getName()).containsExactly("first");
        export.beforeCheckpoint(null);
        tracer.spanBuilder("after-restore").startSpan().end();

        assertThat(made).hasValue(2);
        assertThat(exporters.get(1).getFinishedSpanItems()).extracting(s -> s.getName()).containsExactly("after-restore");
    }

    @Test
    void noneDropsEverySpan() {
        var export = new TraceExport(null);
        var tracer = SdkTracerProvider.builder().addSpanProcessor(SimpleSpanProcessor.create(export)).build()
                .get("test");
        tracer.spanBuilder("dropped").startSpan().end(); // nothing to assert but that nothing fails
        assertThat(export.flush().isSuccess()).isTrue();
    }
}
