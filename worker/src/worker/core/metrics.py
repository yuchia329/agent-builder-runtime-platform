"""The default metrics: records nothing (tests, local runs without METRICS_NAMESPACE)."""


class NoopMetrics:
    def model_cost(self, usd: float) -> None:
        pass
