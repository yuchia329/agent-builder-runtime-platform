"""What the worker must know about each model: price and context window (ADR 0010).

Keyed by the spec's ``<provider>/<model id>``; ``<provider>/*`` covers every model of a
provider. Prices are US dollars per million tokens, standard tier, without cache discounts, so
reported cost errs high. ``context_tokens`` is the model's context window; the loop trims the
conversation well before it (core/trim.py). Check the provider's official pages when adding or
updating a model; a self-hosted model needs an entry too (price 0 if free to run).
"""

from __future__ import annotations

from dataclasses import dataclass

from worker.core.types import Usage


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float

    def cost_usd(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.input_per_million
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000


@dataclass(frozen=True)
class ModelInfo:
    price: Price
    context_tokens: int


MODELS: dict[str, ModelInfo] = {
    "stub/*": ModelInfo(Price(0.0, 0.0), context_tokens=1_000_000),
    # platform.minimax.io/docs/guides/pricing and /api-reference/text-openai-api,
    # checked 2026-11-16 (prices for prompts up to 512k tokens)
    "minimax/MiniMax-M3": ModelInfo(Price(0.30, 1.20), context_tokens=1_000_000),
    "minimax/MiniMax-M3.1-Flash": ModelInfo(Price(0.15, 0.60), context_tokens=1_000_000),
    # Bedrock on-demand, us-east-1, from the AWS price list API (AmazonBedrock offer, published
    # 2026-11-11); context from the model card (docs.aws.amazon.com/nova)
    "bedrock/amazon.nova-lite-v1:0": ModelInfo(Price(0.06, 0.24), context_tokens=300_000),
}


class UnknownModel(Exception):
    """The model is not in MODELS, so its dollar budget and context size are unknown."""


def model_info(model: str, models: dict[str, ModelInfo] = MODELS) -> ModelInfo:
    if model in models:
        return models[model]
    provider = model.partition("/")[0]
    if f"{provider}/*" in models:
        return models[f"{provider}/*"]
    raise UnknownModel(
        f"No price or context size for model '{model}', so its budget cannot be enforced. "
        "Add it to worker/src/worker/core/models.py."
    )
