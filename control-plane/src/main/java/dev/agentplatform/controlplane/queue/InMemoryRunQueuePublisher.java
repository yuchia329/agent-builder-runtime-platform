package dev.agentplatform.controlplane.queue;

import java.util.ArrayList;
import java.util.List;

/** {@link RunQueuePublisher} that keeps message bodies in memory, for tests and local runs. */
public class InMemoryRunQueuePublisher implements RunQueuePublisher {

    private final List<String> bodies = new ArrayList<>();

    @Override
    public synchronized void publish(RunMessage message) {
        bodies.add(message.toJson());
    }

    /** Bodies published so far, oldest first. */
    public synchronized List<String> bodies() {
        return List.copyOf(bodies);
    }
}
