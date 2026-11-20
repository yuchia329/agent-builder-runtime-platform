"""Metrics port (ADR 0023): numbers CloudWatch alarms watch.

Core reports each model call's cost through this port and never sees how it is shipped.
Adapters: CloudWatch embedded metric format on stdout (`adapters/metrics_emf.py`), no-op
(`core/metrics.py`, the default).

A metric carries only its value: no run, tenant, model or other dimension (each dimension
combination is billed as its own metric).
"""

from typing import Protocol


class Metrics(Protocol):
    def model_cost(self, usd: float) -> None:
        """Records one model call's cost in US dollars (0 for a free model). Never raises: a
        metric that cannot be written must not fail the run."""
        ...
