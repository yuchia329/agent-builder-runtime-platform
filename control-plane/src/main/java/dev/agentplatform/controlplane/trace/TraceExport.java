package dev.agentplatform.controlplane.trace;

import io.opentelemetry.sdk.common.CompletableResultCode;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.util.Collection;
import java.util.function.Supplier;
import org.crac.Core;
import org.crac.Resource;

/**
 * Where finished spans go, chosen by {@code TRACING_EXPORTER} ({@link TracingConfig}). The real
 * exporter is made on first use and closed before the SnapStart snapshot, so a restored
 * environment opens its own connections instead of reusing ones from the snapshot. While paused
 * (priming before the snapshot), spans are dropped.
 */
public class TraceExport implements SpanExporter, Resource {

    private final Supplier<SpanExporter> factory;
    private volatile SpanExporter delegate;
    private volatile boolean paused;

    /** Exports through the exporter {@code factory} makes; null drops every span. */
    public TraceExport(Supplier<SpanExporter> factory) {
        this.factory = factory;
        if (factory != null) {
            Core.getGlobalContext().register(this);
        }
    }

    /** Drops spans until {@link #resume()}. */
    public void pause() {
        paused = true;
    }

    public void resume() {
        paused = false;
    }

    @Override
    public CompletableResultCode export(Collection<SpanData> spans) {
        if (factory == null || paused) {
            return CompletableResultCode.ofSuccess();
        }
        return delegate().export(spans);
    }

    private SpanExporter delegate() {
        var current = delegate;
        if (current == null) {
            synchronized (this) {
                current = delegate;
                if (current == null) {
                    current = factory.get();
                    delegate = current;
                }
            }
        }
        return current;
    }

    @Override
    public CompletableResultCode flush() {
        var current = delegate;
        return current == null ? CompletableResultCode.ofSuccess() : current.flush();
    }

    @Override
    public synchronized CompletableResultCode shutdown() {
        var current = delegate;
        delegate = null;
        return current == null ? CompletableResultCode.ofSuccess() : current.shutdown();
    }

    @Override
    public void beforeCheckpoint(org.crac.Context<? extends Resource> context) {
        shutdown(); // the next export after the restore makes a new one
    }

    @Override
    public void afterRestore(org.crac.Context<? extends Resource> context) {
        // Nothing: the exporter is made again on first use.
    }
}
