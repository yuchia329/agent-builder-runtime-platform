import pytest

from worker.core.models import ModelInfo, Price, UnknownModel, model_info
from worker.core.types import Usage


def test_exact_model():
    assert model_info("minimax/MiniMax-M3") == ModelInfo(Price(0.30, 1.20), 1_000_000)


def test_provider_wildcard():
    assert model_info("stub/anything").price == Price(0.0, 0.0)


def test_unknown_model():
    with pytest.raises(UnknownModel, match="minimax/MiniMax-M9"):
        model_info("minimax/MiniMax-M9")


def test_cost_from_usage():
    cost = Price(0.30, 1.20).cost_usd(Usage(input_tokens=1_000_000, output_tokens=500_000))
    assert cost == pytest.approx(0.90)
