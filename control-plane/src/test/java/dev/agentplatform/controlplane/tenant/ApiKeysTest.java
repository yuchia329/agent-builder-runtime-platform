package dev.agentplatform.controlplane.tenant;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.HashSet;
import org.junit.jupiter.api.Test;

class ApiKeysTest {

    @Test
    void keysHaveTheShapeAndAreUnique() {
        var seen = new HashSet<String>();
        for (int i = 0; i < 200; i++) {
            var key = ApiKeys.generate();
            assertThat(key.key()).startsWith("ap_").hasSize(46);
            assertThat(ApiKeys.isWellFormed(key.key())).isTrue();
            assertThat(key.prefix()).isEqualTo(key.key().substring(0, 10));
            assertThat(key.hash()).isEqualTo(ApiKeys.hash(key.key())).matches("^[0-9a-f]{64}$");
            assertThat(seen.add(key.key())).isTrue();
        }
    }

    @Test
    void theHashIsSha256OfTheKey() {
        // echo -n "ap_test" | shasum -a 256
        assertThat(ApiKeys.hash("ap_test"))
                .isEqualTo("d9a681000c2a48aacc3ce3c20bc0ee5811ed67c3de7cb0331624be48c4f0a0d5");
    }

    @Test
    void otherStringsAreNotWellFormed() {
        assertThat(ApiKeys.isWellFormed(null)).isFalse();
        assertThat(ApiKeys.isWellFormed("local-dev-key")).isFalse();
        assertThat(ApiKeys.isWellFormed("ap_short")).isFalse();
        assertThat(ApiKeys.isWellFormed("ap_" + "a".repeat(42) + "!")).isFalse();
        assertThat(ApiKeys.isWellFormed("xx_" + "a".repeat(43))).isFalse();
    }

    @Test
    void toStringNeverShowsTheKey() {
        var key = ApiKeys.generate();

        assertThat(key.toString()).doesNotContain(key.key()).doesNotContain(key.hash()).contains(key.prefix());
    }
}
