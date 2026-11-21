package dev.agentplatform.controlplane.tenant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.store.InMemoryTenantStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class TenantAdminTest {

    private InMemoryTenantStore store;
    private ByteArrayOutputStream out;
    private TenantAdmin admin;

    @BeforeEach
    void setUp() {
        store = new InMemoryTenantStore();
        out = new ByteArrayOutputStream();
        admin = new TenantAdmin(store, new TenantLimits(50, new BigDecimal("5")),
                Clock.fixed(Instant.parse("2026-11-21T10:00:00Z"), ZoneOffset.UTC),
                new PrintStream(out, true, StandardCharsets.UTF_8));
    }

    private int run(String... args) {
        return admin.run(TenantAdmin.Args.parse(args));
    }

    private String printedKey() {
        return out.toString(StandardCharsets.UTF_8).lines().filter(ApiKeys::isWellFormed).reduce((a, b) -> b)
                .orElseThrow();
    }

    @Test
    void createMakesTheTenantAndPrintsAWorkingKeyOnce() {
        assertThat(run("create", "tenant-b", "--max-running", "7", "--daily-usd", "1.5")).isZero();

        var limits = store.find("tenant-b").orElseThrow().limits();
        assertThat(limits.maxRunning()).isEqualTo(7);
        assertThat(limits.dailyUsd()).isEqualByComparingTo("1.5");
        var key = printedKey();
        assertThat(store.tenantForKey(ApiKeys.hash(key))).contains("tenant-b");
        assertThat(out.toString(StandardCharsets.UTF_8).lines().filter(l -> l.contains(key))).hasSize(1);
    }

    @Test
    void createUsesTheDefaultLimits() {
        run("create", "tenant-c");

        assertThat(store.find("tenant-c").orElseThrow().limits().maxRunning()).isEqualTo(50);
    }

    @Test
    void createTwiceFails() {
        run("create", "tenant-b");

        assertThat(run("create", "tenant-b")).isEqualTo(1);
    }

    @Test
    void addKeyGivesASecondKey() {
        run("create", "tenant-b");
        var first = printedKey();

        assertThat(run("add-key", "tenant-b")).isZero();

        var second = printedKey();
        assertThat(second).isNotEqualTo(first);
        assertThat(store.tenantForKey(ApiKeys.hash(first))).contains("tenant-b");
        assertThat(store.tenantForKey(ApiKeys.hash(second))).contains("tenant-b");
    }

    @Test
    void setLimitsChangesOnlyWhatIsGiven() {
        run("create", "tenant-b", "--max-running", "7", "--daily-usd", "1.5");

        assertThat(run("set-limits", "tenant-b", "--daily-usd", "3")).isZero();

        var limits = store.find("tenant-b").orElseThrow().limits();
        assertThat(limits.maxRunning()).isEqualTo(7);
        assertThat(limits.dailyUsd()).isEqualByComparingTo("3");
    }

    @Test
    void unknownTenantOrCommandFails() {
        assertThat(run("set-limits", "nobody", "--max-running", "1")).isEqualTo(1);
        assertThat(run("show", "nobody")).isEqualTo(1);
        assertThat(run("rename", "tenant-b")).isEqualTo(1);
    }

    @Test
    void badArgumentsAreRefused() {
        assertThatThrownBy(() -> TenantAdmin.Args.parse(new String[] {"create"}))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TenantAdmin.Args.parse(new String[] {"create", "Tenant B"}))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(run("create", "tenant-z", "--max-running", "0")).isEqualTo(1);
    }
}
