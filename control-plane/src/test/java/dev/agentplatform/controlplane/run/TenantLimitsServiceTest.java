package dev.agentplatform.controlplane.run;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.agentplatform.controlplane.approval.ApprovalNotifier;
import dev.agentplatform.controlplane.approval.InMemoryApprovalChannel;
import dev.agentplatform.controlplane.queue.InMemoryRunQueuePublisher;
import dev.agentplatform.controlplane.store.IllegalTransitionException;
import dev.agentplatform.controlplane.store.InMemoryAgentStore;
import dev.agentplatform.controlplane.store.InMemoryRunStore;
import dev.agentplatform.controlplane.store.InMemoryTenantStore;
import dev.agentplatform.controlplane.tenant.Tenant;
import dev.agentplatform.controlplane.tenant.Tenant.TenantLimits;
import dev.agentplatform.controlplane.tenant.TenantDirectory;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** Tenant limits as RunService applies them (ADR 0026, ADR 0027), on the in-memory adapters. */
class TenantLimitsServiceTest {

    private static final Instant NOW = Instant.parse("2026-11-21T22:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);

    private InMemoryRunStore runs;
    private InMemoryRunQueuePublisher queue;
    private RunService service;

    @BeforeEach
    void setUp() {
        var agents = new InMemoryAgentStore(CLOCK);
        agents.createAgent("small", "triage");
        agents.publishVersion("small", "triage", "github.issue.opened", "{}");
        var tenants = new InMemoryTenantStore();
        tenants.create(new Tenant("small", new TenantLimits(1, new BigDecimal("0.50")), NOW));
        runs = new InMemoryRunStore();
        queue = new InMemoryRunQueuePublisher();
        var limits = new RunLimits(new TenantDirectory(tenants, new TenantLimits(50, BigDecimal.TEN)), runs, CLOCK, 30);
        service = new RunService(agents, runs, queue, new ApprovalNotifier(new InMemoryApprovalChannel(), runs),
                CLOCK, limits);
    }

    private static RunUpdate to(RunStatus status) {
        return new RunUpdate(status, null, null, null, null, null, null);
    }

    private static RunUpdate running(String costUsd) {
        return new RunUpdate(RunStatus.RUNNING, null, new BigDecimal(costUsd), null, null, null, null);
    }

    private String startRun() {
        return service.start("small", "triage", "{}").runId();
    }

    @Test
    void startAtTheCapIsDeferredAndReQueuedWithADelay() {
        var first = startRun();
        var second = startRun();
        service.update(first, to(RunStatus.RUNNING));
        var sentBefore = queue.sent().size();

        assertThatThrownBy(() -> service.update(second, to(RunStatus.RUNNING)))
                .isInstanceOfSatisfying(RunDeferredException.class,
                        e -> assertThat(e.retryAfterSeconds()).isBetween(24, 36));

        assertThat(runs.find(second).orElseThrow().status()).isEqualTo(RunStatus.QUEUED);
        var resent = queue.sent().subList(sentBefore, queue.sent().size());
        assertThat(resent).hasSize(1);
        assertThat(resent.getFirst().message().runId()).isEqualTo(second);
        assertThat(resent.getFirst().delaySeconds()).isBetween(24, 36);
        // queued-at is when the delayed message is due, so the re-enqueue sweep does not send it early
        assertThat(runs.findQueuedBefore(NOW.plusSeconds(23), 10)).extracting(Run::runId).doesNotContain(second);
        assertThat(runs.findQueuedBefore(NOW.plusSeconds(37), 10)).extracting(Run::runId).contains(second);
    }

    @Test
    void endedRunMakesRoomForTheNext() {
        var first = startRun();
        var second = startRun();
        service.update(first, to(RunStatus.RUNNING));
        service.update(first, to(RunStatus.SUCCEEDED));

        assertThat(service.update(second, to(RunStatus.RUNNING)).status()).isEqualTo(RunStatus.RUNNING);
    }

    @Test
    void resumeAtTheCapIsNotDeferred() {
        var run = startRun();
        service.update(run, to(RunStatus.RUNNING));

        assertThat(service.update(run, to(RunStatus.RUNNING)).status()).isEqualTo(RunStatus.RUNNING);
    }

    @Test
    void overTheDailyCapNewRunsAreRefusedAndQueuedOnesFail() {
        var spender = startRun();
        var waiting = startRun();
        service.update(spender, running("0.50"));
        service.update(spender, to(RunStatus.SUCCEEDED));

        assertThatThrownBy(this::startRun).isInstanceOfSatisfying(TenantBudgetExceededException.class,
                e -> assertThat(e.retryAfterSeconds()).isEqualTo((int) Duration.ofHours(2).toSeconds()));
        assertThatThrownBy(() -> service.update(waiting, to(RunStatus.RUNNING)))
                .isInstanceOf(IllegalTransitionException.class);
        var failed = runs.find(waiting).orElseThrow();
        assertThat(failed.status()).isEqualTo(RunStatus.FAILED);
        assertThat(failed.failure().reason()).isEqualTo("tenant_budget");
    }

    @Test
    void runAlreadyExecutingFinishesOverTheCap() {
        var run = startRun();
        service.update(run, running("0.60"));

        assertThat(service.update(run, running("0.70")).status()).isEqualTo(RunStatus.RUNNING);
        assertThat(service.update(run, to(RunStatus.SUCCEEDED)).status()).isEqualTo(RunStatus.SUCCEEDED);
    }

    @Test
    void tenantWithoutRecordGetsTheDefaults() {
        assertThat(new RunLimits(new TenantDirectory(new InMemoryTenantStore(), new TenantLimits(50, BigDecimal.TEN)),
                runs, CLOCK, 30).of("unknown")).isEqualTo(new TenantLimits(50, BigDecimal.TEN));
    }

    @Test
    void deferSetsTheRunAsideAndFreesItsSlot() {
        var run = startRun();
        var other = startRun();
        service.update(run, to(RunStatus.RUNNING));

        var queued = service.defer(run, 120);

        assertThat(queued.status()).isEqualTo(RunStatus.QUEUED);
        assertThat(queued.setAsideSeconds()).isEqualTo(120);
        assertThat(queue.sent().getLast().message().runId()).isEqualTo(run);
        assertThat(queue.sent().getLast().delaySeconds()).isEqualTo(120);
        assertThat(service.update(other, to(RunStatus.RUNNING)).status()).isEqualTo(RunStatus.RUNNING);
    }

    @Test
    void afterFifteenMinutesSetAsideTheRunFailsRateLimited() {
        var run = startRun();
        service.update(run, to(RunStatus.RUNNING));
        service.defer(run, 900);
        service.update(run, to(RunStatus.RUNNING));
        var sentBefore = queue.sent().size();

        var failed = service.defer(run, 60);

        assertThat(failed.status()).isEqualTo(RunStatus.FAILED);
        assertThat(failed.failure().reason()).isEqualTo("rate_limited");
        assertThat(queue.sent()).hasSize(sentBefore);
        assertThat(runs.running("small")).isZero();
    }

    @Test
    void onlyARunningRunIsDeferred() {
        var run = startRun();

        assertThatThrownBy(() -> service.defer(run, 30)).isInstanceOf(IllegalTransitionException.class);
    }

    @Test
    void capRetryDelayIsSpreadAroundTheSetting() {
        var limits = new RunLimits(new TenantDirectory(new InMemoryTenantStore(), new TenantLimits(1, BigDecimal.ONE)),
                runs, CLOCK, 30);
        for (int i = 0; i < 200; i++) {
            assertThat(limits.capRetryDelaySeconds()).isBetween(24, 36);
        }
        var tiny = new RunLimits(new TenantDirectory(new InMemoryTenantStore(), new TenantLimits(1, BigDecimal.ONE)),
                runs, CLOCK, 1);
        assertThat(tiny.capRetryDelaySeconds()).isEqualTo(1);
    }
}
