package dev.agentplatform.controlplane.run;

import java.math.BigDecimal;

/** A status change plus progress, as reported by a worker. Null fields are not changed. */
public record RunUpdate(
        RunStatus status,
        Integer steps,
        BigDecimal costUsd,
        String traceId,
        String pendingApprovalJson,
        Run.Failure failure,
        String output) {}
