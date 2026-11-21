package dev.agentplatform.controlplane.queue;

import java.util.ArrayList;
import java.util.List;

/** {@link RunQueuePublisher} that keeps message bodies in memory, for tests and local runs. */
public class InMemoryRunQueuePublisher implements RunQueuePublisher {

    /** A published message and the delay it was sent with. */
    public record Sent(RunMessage message, int delaySeconds) {}

    private final List<Sent> sent = new ArrayList<>();

    @Override
    public synchronized void publish(RunMessage message, int delaySeconds) {
        if (delaySeconds < 0 || delaySeconds > MAX_DELAY_SECONDS) {
            throw new IllegalArgumentException("delaySeconds must be 0 to " + MAX_DELAY_SECONDS);
        }
        sent.add(new Sent(message, delaySeconds));
    }

    /** Bodies published so far, oldest first. */
    public synchronized List<String> bodies() {
        return sent.stream().map(s -> s.message().toJson()).toList();
    }

    /** Messages (body fields and trace context) published so far, oldest first. */
    public synchronized List<RunMessage> messages() {
        return sent.stream().map(Sent::message).toList();
    }

    /** Messages with their delays, oldest first. */
    public synchronized List<Sent> sent() {
        return List.copyOf(sent);
    }
}
