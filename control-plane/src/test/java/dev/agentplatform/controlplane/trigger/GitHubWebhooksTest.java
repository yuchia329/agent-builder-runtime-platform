package dev.agentplatform.controlplane.trigger;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class GitHubWebhooksTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final byte[] BODY = "{\"action\":\"opened\"}".getBytes(StandardCharsets.UTF_8);

    static ObjectNode issuesOpened() throws Exception {
        return (ObjectNode) JSON.readTree(Files.readString(Path.of("src/test/resources/github/issues-opened.json")));
    }

    @Test
    void signatureMatchesGitHubsExample() {
        // Example from GitHub's "Validating webhook deliveries" docs.
        var body = "Hello, World!".getBytes(StandardCharsets.UTF_8);
        var header = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17";

        assertThat(GitHubWebhooks.signatureMatches("It's a Secret to Everybody", body, header)).isTrue();
        assertThat(GitHubWebhooks.sign("It's a Secret to Everybody", body)).isEqualTo(header);
    }

    @Test
    void signatureRejectsWrongSecretBodyOrFormat() {
        var good = GitHubWebhooks.sign("secret", BODY);

        assertThat(GitHubWebhooks.signatureMatches("secret", BODY, good)).isTrue();
        assertThat(GitHubWebhooks.signatureMatches("other", BODY, good)).isFalse();
        assertThat(GitHubWebhooks.signatureMatches("secret", "{}".getBytes(StandardCharsets.UTF_8), good)).isFalse();
        assertThat(GitHubWebhooks.signatureMatches("secret", BODY, null)).isFalse();
        assertThat(GitHubWebhooks.signatureMatches("secret", BODY, good.substring(7))).isFalse();
        assertThat(GitHubWebhooks.signatureMatches("secret", BODY, "sha256=not-hex")).isFalse();
        assertThat(GitHubWebhooks.signatureMatches("secret", BODY, "sha1=" + good.substring(7))).isFalse();
    }

    @Test
    void triggerNames() {
        assertThat(GitHubWebhooks.triggerName("issues", JSON.readTree("{\"action\":\"opened\"}")))
                .isEqualTo("github.issue.opened");
        assertThat(GitHubWebhooks.triggerName("issue_comment", JSON.readTree("{\"action\":\"created\"}")))
                .isEqualTo("github.issue_comment.created");
        assertThat(GitHubWebhooks.triggerName("push", JSON.readTree("{}"))).isEqualTo("github.push");
    }

    @Test
    void issueInputKeepsOnlyWhatAgentsNeed() throws Exception {
        var input = GitHubWebhooks.runInput("issues", issuesOpened());

        assertThat(input).isEqualTo(JSON.readTree("""
                {"action": "opened",
                 "repository": {"full_name": "octocat/Hello-World"},
                 "issue": {"number": 4, "title": "EUR card refunds return an error",
                           "body": "Refund failed: currency not supported. USD refunds work.",
                           "state": "open", "html_url": "https://github.com/octocat/Hello-World/issues/4",
                           "labels": ["needs-triage"], "user": {"login": "octocat"}},
                 "sender": {"login": "octocat"}}
                """));
    }

    @Test
    void issueInputToleratesMissingFields() {
        var input = GitHubWebhooks.runInput("issues", (ObjectNode) JSON.readTree("{\"action\":\"opened\"}"));

        assertThat(input).isEqualTo(JSON.readTree("{\"action\":\"opened\"}"));
    }

    @Test
    void otherEventsPassThrough() {
        var payload = (ObjectNode) JSON.readTree("{\"ref\":\"refs/heads/main\",\"commits\":[]}");

        assertThat(GitHubWebhooks.runInput("push", payload)).isEqualTo(payload);
    }
}
