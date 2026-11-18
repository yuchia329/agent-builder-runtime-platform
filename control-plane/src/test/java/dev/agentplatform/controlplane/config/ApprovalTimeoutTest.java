package dev.agentplatform.controlplane.config;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.ControlPlaneApplication;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;

/** APPROVAL_TIMEOUT_HOURS: default 72, must stay under the worker's 15-day retention (ADR 0011). */
class ApprovalTimeoutTest {

    private static Throwable startWith(String hours) {
        var app = new SpringApplication(ControlPlaneApplication.class);
        app.setWebApplicationType(WebApplicationType.NONE);
        try (var context = app.run("--platform.approval.timeout-hours=" + hours, "--platform.tenant.api-key=k")) {
            assertThat(context.getBean(PlatformProperties.class).approvalTimeout())
                    .isEqualTo(Duration.ofHours(Long.parseLong(hours)));
            return null;
        } catch (RuntimeException e) {
            return e;
        }
    }

    @Test
    void defaultIs72Hours() {
        var props = new PlatformProperties(null, "t", null, null, null, null, null, null, null);
        assertThat(props.approvalTimeout()).isEqualTo(Duration.ofHours(72));
    }

    @Test
    void validRangeStarts() {
        assertThat(startWith("1")).isNull();
        assertThat(startWith("359")).isNull();
    }

    @Test
    void zeroOr15DaysFailsStartup() {
        for (var hours : new String[] {"0", "360"}) {
            assertThat(startWith(hours)).as(hours).isNotNull()
                    .rootCause().hasMessageContaining("timeoutHours");
        }
    }
}
