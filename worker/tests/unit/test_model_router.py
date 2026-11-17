import pytest

from worker.adapters.model_router import ModelRouter
from worker.adapters.model_stub import ScriptedModel, final_turn
from worker.core.errors import ModelError
from worker.core.types import Message


def test_forwards_model_id_without_provider():
    minimax = ScriptedModel([final_turn("hi")])
    router = ModelRouter({"minimax": minimax})

    router.complete("minimax/MiniMax-M3", "s", [Message.user("x")], [])

    assert minimax.calls[0].model == "MiniMax-M3"


def test_model_id_may_contain_slashes():
    openrouter = ScriptedModel([final_turn("hi")])

    ModelRouter({"openrouter": openrouter}).complete("openrouter/minimax/minimax-m3", "s", [], [])

    assert openrouter.calls[0].model == "minimax/minimax-m3"


def test_unknown_provider_names_the_configured_ones():
    router = ModelRouter({"stub": ScriptedModel([]), "minimax": ScriptedModel([])})

    with pytest.raises(ModelError, match=r"'openai' is not configured.*minimax, stub"):
        router.complete("openai/gpt-5", "s", [], [])


@pytest.mark.parametrize("model", ["MiniMax-M3", "minimax/"])
def test_malformed_model_is_model_error(model):
    with pytest.raises(ModelError, match="<provider>/<model id>"):
        ModelRouter({"minimax": ScriptedModel([])}).complete(model, "s", [], [])
