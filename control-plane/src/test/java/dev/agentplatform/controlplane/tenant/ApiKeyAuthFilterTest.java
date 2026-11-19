package dev.agentplatform.controlplane.tenant;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.secret.SecretUnavailableException;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** The filter when the secret store cannot be read or holds no key. */
class ApiKeyAuthFilterTest {

    private static final PlatformProperties PROPS = new PlatformProperties(null, "t", null, null,
            new PlatformProperties.TenantProperties("t1", null), null, null, null, null, null, null);

    private static SecretSource source(String apiKey, boolean failing) {
        return new SecretSource() {
            @Override
            public String get(Secret secret) {
                if (failing) {
                    throw new SecretUnavailableException("SSM down", null);
                }
                return secret == Secret.TENANT_API_KEY ? apiKey : null;
            }

            @Override
            public String peek(Secret secret) {
                return null;
            }

            @Override
            public boolean configured(Secret secret) {
                return true;
            }
        };
    }

    private static MockHttpServletResponse call(SecretSource secrets, String key) throws Exception {
        var request = new MockHttpServletRequest("GET", "/agents");
        if (key != null) {
            request.addHeader(ApiKeyAuthFilter.HEADER, key);
        }
        var response = new MockHttpServletResponse();
        new ApiKeyAuthFilter(PROPS, secrets).doFilter(request, response, new MockFilterChain());
        return response;
    }

    @Test
    void anUnreadableStoreGives503() throws Exception {
        var response = call(source("k", true), "k");

        assertThat(response.getStatus()).isEqualTo(503);
        assertThat(response.getContentAsString()).doesNotContain("SSM down");
    }

    @Test
    void noStoredKeyRefusesEveryKey() throws Exception {
        assertThat(call(source(null, false), "anything").getStatus()).isEqualTo(401);
    }

    @Test
    void noPresentedKeyNeverReadsTheStore() throws Exception {
        assertThat(call(source("k", true), null).getStatus()).isEqualTo(401);
    }

    @Test
    void theStoredKeyPasses() throws Exception {
        assertThat(call(source("k", false), "k").getStatus()).isEqualTo(200);
    }
}
