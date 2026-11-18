package dev.agentplatform.controlplane.approval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/**
 * Behavior every {@link ApprovalChannel} adapter must have (ADR 0004). Subclasses supply a
 * working channel and make the next post fail.
 */
abstract class ApprovalChannelContractTest {

    static final ApprovalRequest REQUEST = new ApprovalRequest("run_" + "0".repeat(32), "t1", "issue-triage", 5,
            "github.close_issue", JsonMapper.builder().build().readTree(
                    "{\"repo\":\"octocat/Hello-World\",\"issue_number\":7,\"reason\":\"duplicate\",\"duplicate_of\":1}"));

    protected abstract ApprovalChannel channel();

    /** Makes the next post fail the way this adapter's channel fails. */
    protected abstract void failNextPost();

    @Test
    void postReturnsWhereTheRequestWent() {
        var message = channel().post(REQUEST);

        assertThat(message.channel()).isNotBlank();
        assertThat(message.messageId()).isNotBlank();
    }

    @Test
    void failedPostThrowsApprovalChannelException() {
        failNextPost();

        assertThatThrownBy(() -> channel().post(REQUEST)).isInstanceOf(ApprovalChannelException.class);
    }
}
