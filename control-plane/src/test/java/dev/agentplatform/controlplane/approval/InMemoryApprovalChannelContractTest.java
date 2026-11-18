package dev.agentplatform.controlplane.approval;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class InMemoryApprovalChannelContractTest extends ApprovalChannelContractTest {

    private final InMemoryApprovalChannel channel = new InMemoryApprovalChannel();

    @Override
    protected ApprovalChannel channel() {
        return channel;
    }

    @Override
    protected void failNextPost() {
        channel.failNext(1);
    }

    @Test
    void keepsPostedRequestsInOrder() {
        channel.post(REQUEST);
        channel.post(REQUEST);

        assertThat(channel.posted()).containsExactly(REQUEST, REQUEST);
    }
}
