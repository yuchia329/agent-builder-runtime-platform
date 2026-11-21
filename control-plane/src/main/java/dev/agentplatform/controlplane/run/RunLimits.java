package dev.agentplatform.controlplane.run;

import dev.agentplatform.controlplane.config.PlatformProperties;
import dev.agentplatform.controlplane.queue.RunQueuePublisher;
import dev.agentplatform.controlplane.store.InMemoryTenantStore;
import dev.agentplatform.controlplane.store.RunStore;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import dev.agentplatform.controlplane.tenant.TenantDirectory;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.concurrent.ThreadLocalRandom;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Tenant limits as {@link RunService} applies them (ADR 0026, ADR 0027): each tenant's caps, its
 * spend today (UTC), and the delays of runs refused at the cap or set aside.
 */
@Component
public class RunLimits {

    /** A run set aside this long in total fails {@code rate_limited} instead (ADR 0027). */
    public static final int MAX_SET_ASIDE_SECONDS = 900;

    private final TenantDirectory tenants;
    private final RunStore runs;
    private final Clock clock;
    private final int capRetrySeconds;

    @Autowired
    public RunLimits(TenantDirectory tenants, RunStore runs, Clock clock, PlatformProperties props) {
        this(tenants, runs, clock, props.tenantCapRetrySeconds());
    }

    public RunLimits(TenantDirectory tenants, RunStore runs, Clock clock, int capRetrySeconds) {
        this.tenants = tenants;
        this.runs = runs;
        this.clock = clock;
        this.capRetrySeconds = capRetrySeconds;
    }

    /** Limits too high to reach, for tests that do not look at them. */
    public static RunLimits unlimited(RunStore runs, Clock clock) {
        var none = new TenantLimits(Integer.MAX_VALUE, new BigDecimal("1000000"));
        return new RunLimits(new TenantDirectory(new InMemoryTenantStore(), none), runs, clock, 30);
    }

    public TenantLimits of(String tenantId) {
        return tenants.limits(tenantId);
    }

    /** True once the tenant's spend today (UTC) has reached its daily cap. */
    public boolean overDailyCap(String tenantId, TenantLimits limits) {
        return runs.spend(tenantId, LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC))
                .compareTo(limits.dailyUsd()) >= 0;
    }

    /** @throws TenantBudgetExceededException if the tenant has reached its daily spend cap */
    public void requireUnderDailyCap(String tenantId) {
        var limits = of(tenantId);
        if (overDailyCap(tenantId, limits)) {
            throw new TenantBudgetExceededException(tenantId, limits.dailyUsd(), secondsUntilNextDay());
        }
    }

    /**
     * Delay before a run refused at the cap arrives again: the setting, spread by up to 20% so
     * runs refused together do not all come back in the same second.
     */
    public int capRetryDelaySeconds() {
        var spread = capRetrySeconds / 5;
        var delay = capRetrySeconds + ThreadLocalRandom.current().nextInt(-spread, spread + 1);
        return Math.clamp(delay, 1, RunQueuePublisher.MAX_DELAY_SECONDS);
    }

    int secondsUntilNextDay() {
        var now = clock.instant();
        var tomorrow = LocalDate.ofInstant(now, ZoneOffset.UTC).plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant();
        return (int) Math.max(1, Duration.between(now, tomorrow).toSeconds());
    }
}
