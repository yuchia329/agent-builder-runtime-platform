package dev.agentplatform.controlplane.tenant;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.secret.Secret;
import dev.agentplatform.controlplane.secret.SecretSource;
import dev.agentplatform.controlplane.secret.SecretUnavailableException;
import dev.agentplatform.controlplane.store.InMemoryTenantStore;
import dev.agentplatform.controlplane.store.TenantStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** The filter: configured key first, then stored keys by hash; failures and the lookups it skips. */
class ApiKeyAuthFilterTest {

    private static final PlatformProperties PROPS = new PlatformProperties(null, "t", null, null,
            new PlatformProperties.TenantProperties("t1", null, 50, BigDecimal.TEN), null, null, null, null, null, null);
    private static final Instant NOW = Instant.parse("2026-11-21T10:00:00Z");

    private InMemoryTenantStore store;
    private String storedKey;

    @BeforeEach
    void setUp() {
        store = new InMemoryTenantStore();
        store.create(new Tenant("tenant-b", new TenantLimits(5, BigDecimal.ONE), NOW));
        var key = ApiKeys.generate();
        store.addApiKey("tenant-b", key.hash(), key.prefix(), NOW);
        storedKey = key.key();
    }

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

    private record Result(int status, String body, Object tenant) {}

    private static Result call(SecretSource secrets, TenantStore tenants, String key) throws Exception {
        var request = new MockHttpServletRequest("GET", "/agents");
        if (key != null) {
            request.addHeader(ApiKeyAuthFilter.HEADER, key);
        }
        var response = new MockHttpServletResponse();
        new ApiKeyAuthFilter(PROPS, secrets, tenants).doFilter(request, response, new MockFilterChain());
        return new Result(response.getStatus(), response.getContentAsString(),
                request.getAttribute(ApiKeyAuthFilter.TENANT_ATTRIBUTE));
    }

    @Test
    void theConfiguredKeyNamesTheConfiguredTenant() throws Exception {
        var result = call(source("k", false), store, "k");

        assertThat(result.status()).isEqualTo(200);
        assertThat(result.tenant()).isEqualTo("t1");
    }

    @Test
    void aStoredKeyNamesItsTenant() throws Exception {
        var result = call(source("k", false), store, storedKey);

        assertThat(result.status()).isEqualTo(200);
        assertThat(result.tenant()).isEqualTo("tenant-b");
    }

    @Test
    void anUnknownWellFormedKeyIs401() throws Exception {
        assertThat(call(source("k", false), store, ApiKeys.generate().key()).status()).isEqualTo(401);
    }

    @Test
    void aKeyNotShapedLikeOursNeverReachesTheStore() throws Exception {
        var lookups = new AtomicInteger();
        var counting = new CountingStore(store, lookups);

        assertThat(call(source("k", false), counting, "anything").status()).isEqualTo(401);
        assertThat(call(source("k", false), counting, "priming").status()).isEqualTo(401);
        assertThat(lookups).hasValue(0);
    }

    @Test
    void theConfiguredKeyNeedsNoStoreLookup() throws Exception {
        var lookups = new AtomicInteger();

        assertThat(call(source("k", false), new CountingStore(store, lookups), "k").status()).isEqualTo(200);
        assertThat(lookups).hasValue(0);
    }

    @Test
    void aStoredKeyStillWorksWhenTheSecretIsUnreadable() throws Exception {
        var result = call(source("k", true), store, storedKey);

        assertThat(result.status()).isEqualTo(200);
        assertThat(result.tenant()).isEqualTo("tenant-b");
    }

    @Test
    void anUnreadableSecretGives503ForOtherKeys() throws Exception {
        var result = call(source("k", true), store, "k");

        assertThat(result.status()).isEqualTo(503);
        assertThat(result.body()).doesNotContain("SSM down");
    }

    @Test
    void anUnreadableTenantStoreGives503() throws Exception {
        var failing = new CountingStore(store, new AtomicInteger()) {
            @Override
            public Optional<String> tenantForKey(String keyHash) {
                throw new IllegalStateException("DynamoDB down " + keyHash);
            }
        };

        var result = call(source("k", false), failing, storedKey);

        assertThat(result.status()).isEqualTo(503);
        assertThat(result.body()).doesNotContain("DynamoDB").doesNotContain(ApiKeys.hash(storedKey));
    }

    @Test
    void noConfiguredKeyStillAcceptsStoredKeys() throws Exception {
        assertThat(call(source(null, false), store, "anything").status()).isEqualTo(401);
        assertThat(call(source(null, false), store, storedKey).status()).isEqualTo(200);
    }

    @Test
    void noPresentedKeyReadsNothing() throws Exception {
        var lookups = new AtomicInteger();

        assertThat(call(source("k", true), new CountingStore(store, lookups), null).status()).isEqualTo(401);
        assertThat(lookups).hasValue(0);
    }

    /** Delegates to a store and counts key lookups. */
    private static class CountingStore implements TenantStore {
        private final TenantStore delegate;
        private final AtomicInteger lookups;

        CountingStore(TenantStore delegate, AtomicInteger lookups) {
            this.delegate = delegate;
            this.lookups = lookups;
        }

        @Override
        public void create(Tenant tenant) {
            delegate.create(tenant);
        }

        @Override
        public Optional<Tenant> find(String tenantId) {
            return delegate.find(tenantId);
        }

        @Override
        public void setLimits(String tenantId, TenantLimits limits) {
            delegate.setLimits(tenantId, limits);
        }

        @Override
        public void addApiKey(String tenantId, String keyHash, String keyPrefix, Instant now) {
            delegate.addApiKey(tenantId, keyHash, keyPrefix, now);
        }

        @Override
        public Optional<String> tenantForKey(String keyHash) {
            lookups.incrementAndGet();
            return delegate.tenantForKey(keyHash);
        }
    }
}
