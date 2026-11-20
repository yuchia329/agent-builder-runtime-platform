package dev.agentplatform.controlplane.trace;

import io.opentelemetry.api.trace.SpanId;
import io.opentelemetry.api.trace.TraceId;
import io.opentelemetry.sdk.trace.IdGenerator;
import java.security.SecureRandom;

/**
 * Trace and span ids from {@link SecureRandom}. OpenTelemetry's default generator uses
 * {@code ThreadLocalRandom}, whose state is in the SnapStart snapshot: every environment restored
 * from it would then make the same ids (ADR 0017, ADR 0022).
 */
final class SecureIdGenerator implements IdGenerator {

    private static final SecureRandom RANDOM = new SecureRandom();

    @Override
    public String generateSpanId() {
        long id;
        do {
            id = RANDOM.nextLong();
        } while (id == 0);
        return SpanId.fromLong(id);
    }

    @Override
    public String generateTraceId() {
        long high;
        long low;
        do {
            high = RANDOM.nextLong();
            low = RANDOM.nextLong();
        } while (high == 0 && low == 0);
        return TraceId.fromLongs(high, low);
    }
}
