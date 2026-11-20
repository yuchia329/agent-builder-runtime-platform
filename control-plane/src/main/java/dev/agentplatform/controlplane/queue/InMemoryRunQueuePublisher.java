package dev.agentplatform.controlplane.queue;

import java.util.ArrayList;
import java.util.List;

/** {@link RunQueuePublisher} that keeps message bodies in memory, for tests and local runs. */
public class InMemoryRunQueuePublisher implements RunQueuePublisher {

    private final List<RunMessage> messages = new ArrayList<>();

    @Override
    public synchronized void publish(RunMessage message) {
        messages.add(message);
    }

    /** Bodies published so far, oldest first. */
    public synchronized List<String> bodies() {
        return messages.stream().map(RunMessage::toJson).toList();
    }

    /** Messages (body fields and trace context) published so far, oldest first. */
    public synchronized List<RunMessage> messages() {
        return List.copyOf(messages);
    }
}
