package dev.agentplatform.controlplane.approval;

import static dev.agentplatform.controlplane.approval.FakeSlackServer.fixture;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** The Slack adapter against a fake Slack replaying recorded replies. */
class SlackApprovalChannelContractTest extends ApprovalChannelContractTest {

    static final String TOKEN = "xoxb-test-secret-token";
    static final String CHANNEL = "C0123456789";

    private final FakeSlackServer slack = new FakeSlackServer();
    private final SlackApprovalChannel channel = new SlackApprovalChannel(slack.apiUrl(), TOKEN, CHANNEL);

    @AfterEach
    void stop() {
        slack.close();
    }

    @Override
    protected ApprovalChannel channel() {
        return channel;
    }

    @Override
    protected void failNextPost() {
        slack.answer(fixture("post_message_channel_not_found"));
    }

    @Test
    void postsTheMessageWithTheBotToken() {
        var message = channel.post(REQUEST);

        var request = slack.requests().getFirst();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/api/chat.postMessage");
        assertThat(request.header("Authorization")).isEqualTo("Bearer " + TOKEN);
        assertThat(request.header("Content-Type")).isEqualTo("application/json; charset=utf-8");
        assertThat(request.body()).isEqualTo(SlackApprovalMessage.body(CHANNEL, REQUEST));
        assertThat(message).isEqualTo(new ApprovalMessage(CHANNEL, "1794980400.000100")); // from the recording
    }

    @Test
    void outcomeReplacesTheButtonsWithChatUpdate() {
        slack.answer(fixture("update_message"));

        channel.showOutcome(MESSAGE, REQUEST, APPROVED);

        var request = slack.requests().getLast();
        assertThat(request.path()).isEqualTo("/api/chat.update");
        assertThat(request.header("Authorization")).isEqualTo("Bearer " + TOKEN);
        assertThat(request.body()).isEqualTo(SlackApprovalMessage.decided(MESSAGE, REQUEST, APPROVED));
    }

    @ParameterizedTest
    @CsvSource({
            "post_message_channel_not_found, channel_not_found",
            "post_message_invalid_blocks, invalid_blocks",
            "post_message_bad_token, internal_error"})
    void okFalseIsAFailureEvenWithHttp200(String recorded, String error) {
        slack.answer(fixture(recorded));

        assertThatThrownBy(() -> channel.post(REQUEST))
                .isInstanceOf(ApprovalChannelException.class)
                .hasMessage("Slack refused the message: " + error); // no token in any message
    }

    @Test
    void rateLimitSaysWhenToRetry() {
        slack.answer(429, "", Map.of("Retry-After", "30")); // Slack docs: 429 with Retry-After seconds

        assertThatThrownBy(() -> channel.post(REQUEST)).hasMessage("Slack rate limit; retry after 30 s");
    }

    @Test
    void serverErrorAndNonJsonAreFailures() {
        slack.answer(503, "upstream down", Map.of());
        assertThatThrownBy(() -> channel.post(REQUEST)).hasMessage("Slack answered HTTP 503");

        slack.answer("<html>maintenance</html>");
        assertThatThrownBy(() -> channel.post(REQUEST)).hasMessage("Slack answered with a body that is not JSON");
    }

    @Test
    void unreachableSlackIsAFailure() {
        var url = slack.apiUrl();
        slack.close();

        assertThatThrownBy(() -> new SlackApprovalChannel(url, TOKEN, CHANNEL).post(REQUEST))
                .isInstanceOf(ApprovalChannelException.class)
                .hasMessageStartingWith("Slack is unreachable");
    }

    @Test
    void tokenAndChannelAreRequired() {
        assertThatThrownBy(() -> new SlackApprovalChannel(slack.apiUrl(), " ", CHANNEL))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new SlackApprovalChannel(slack.apiUrl(), TOKEN, ""))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
