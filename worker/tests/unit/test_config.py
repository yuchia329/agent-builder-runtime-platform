import pytest

from worker.config import Config, ConfigError

LOCAL = {"RUN_QUEUE_URL": "http://localhost:9324/000000000000/runs"}


def test_defaults_for_local_development():
    config = Config.from_env(LOCAL)

    assert config.queue_backend == "sqs"
    assert config.control_plane_url == "http://localhost:8080"
    assert config.model_providers == ()  # only the built-in stub
    assert config.tools_provider == "fake"
    assert config.concurrency == 4
    assert config.visibility_seconds == 60
    assert config.state_backend == "dynamodb"
    assert config.state_table == "run-state"


def test_sqs_needs_queue_url():
    with pytest.raises(ConfigError, match="RUN_QUEUE_URL"):
        Config.from_env({})


def test_memory_queue_needs_no_url():
    assert Config.from_env({"QUEUE_BACKEND": "memory"}).run_queue_url is None


def test_reports_every_bad_value_at_once():
    env = {
        **LOCAL,
        "MODEL_PROVIDERS": "Mini-Max",
        "WORKER_CONCURRENCY": "0",
        "QUEUE_BACKEND": "kafka",
        "STATE_BACKEND": "redis",
    }

    with pytest.raises(ConfigError) as e:
        Config.from_env(env)

    for name in ("MODEL_PROVIDERS", "WORKER_CONCURRENCY", "QUEUE_BACKEND", "STATE_BACKEND"):
        assert name in str(e.value)


def test_wiring_builds_sqs_queue_in_configured_region(monkeypatch):
    # boto3 ignores AWS_REGION; without an explicit region a container with no ~/.aws fails.
    from worker.wiring import build

    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "local")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local")

    app = build(Config.from_env({**LOCAL, "AWS_REGION": "eu-west-1"}))

    assert app.queue._sqs.meta.region_name == "eu-west-1"
    assert app.state._db.meta.region_name == "eu-west-1"
    assert app.state._table == "run-state"


def test_gateway_signing_is_off_unless_asked():
    gateway = {"QUEUE_BACKEND": "memory", "TOOLS_PROVIDER": "mcp", "TOOL_GATEWAY_URL": "http://g"}

    assert Config.from_env(gateway).tool_gateway_sigv4 is False
    assert Config.from_env({**gateway, "TOOL_GATEWAY_SIGV4": "true"}).tool_gateway_sigv4 is True
    with pytest.raises(ConfigError, match="TOOL_GATEWAY_SIGV4"):
        Config.from_env({**gateway, "TOOL_GATEWAY_SIGV4": "yes"})


def test_wiring_signs_gateway_requests_when_asked():
    from worker.adapters.tools_mcp import SignedAuth
    from worker.wiring import build

    env = {"QUEUE_BACKEND": "memory", "STATE_BACKEND": "memory", "TOOLS_PROVIDER": "mcp"}
    env["TOOL_GATEWAY_URL"] = "http://g"
    signed = build(Config.from_env({**env, "TOOL_GATEWAY_SIGV4": "true"}))
    unsigned = build(Config.from_env(env))

    assert isinstance(signed.deps.tools._auth, SignedAuth)
    assert unsigned.deps.tools._auth is None


def test_wiring_builds_memory_state_store():
    from worker.adapters.run_state_memory import InMemoryRunStateStore
    from worker.wiring import build

    app = build(Config.from_env({"QUEUE_BACKEND": "memory", "STATE_BACKEND": "memory"}))

    assert isinstance(app.state, InMemoryRunStateStore)


MINIMAX = {
    "MODEL_PROVIDERS": "minimax",
    "MODEL_MINIMAX_PROTOCOL": "openai",
    "MODEL_MINIMAX_BASE_URL": "https://api.minimax.io/v1",
    "MODEL_MINIMAX_API_KEY": "sk-secret",
    "MODEL_MINIMAX_EXTRA_BODY": '{"reasoning_split": true}',
}


def test_model_provider_from_env():
    [provider] = Config.from_env({**LOCAL, **MINIMAX}).model_providers

    assert provider.name == "minimax"
    assert provider.protocol == "openai"
    assert provider.base_url == "https://api.minimax.io/v1"
    assert provider.api_key == "sk-secret"
    assert provider.max_tokens == 4096
    assert provider.extra_body == {"reasoning_split": True}


def test_api_key_is_not_in_repr():
    config = Config.from_env({**LOCAL, **MINIMAX})
    assert "sk-secret" not in repr(config)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"MODEL_MINIMAX_PROTOCOL": "grpc"}, "MODEL_MINIMAX_PROTOCOL"),
        ({"MODEL_MINIMAX_BASE_URL": ""}, "MODEL_MINIMAX_BASE_URL is required"),
        ({"MODEL_MINIMAX_MAX_TOKENS": "lots"}, "MODEL_MINIMAX_MAX_TOKENS"),
        ({"MODEL_MINIMAX_EXTRA_BODY": "[1]"}, "MODEL_MINIMAX_EXTRA_BODY"),
        ({"MODEL_PROVIDERS": "minimax,stub"}, "'stub' is built in"),
        ({"MODEL_PROVIDER": "stub"}, "replaced by MODEL_PROVIDERS"),
    ],
)
def test_bad_model_provider_config(changes, expected):
    with pytest.raises(ConfigError, match=expected) as e:
        Config.from_env({**LOCAL, **MINIMAX, **changes})
    assert "sk-secret" not in str(e.value)


def test_wiring_routes_stub_and_configured_providers():
    from worker.wiring import build

    app = build(Config.from_env({**MINIMAX, "QUEUE_BACKEND": "memory"}))

    assert sorted(app.deps.model._providers) == ["minimax", "stub"]


BEDROCK = {
    "MODEL_PROVIDERS": "bedrock",
    "MODEL_BEDROCK_PROTOCOL": "bedrock",
    "MODEL_BEDROCK_AWS_PROFILE": "default",
    "MODEL_BEDROCK_TEMPERATURE": "0",
    "MODEL_BEDROCK_EXTRA_BODY": '{"inferenceConfig": {"topK": 1}}',
}


def test_bedrock_provider_needs_no_base_url_and_defaults_to_aws_region():
    [provider] = Config.from_env({**LOCAL, **BEDROCK, "AWS_REGION": "us-west-2"}).model_providers

    assert provider.protocol == "bedrock"
    assert provider.base_url == ""
    assert provider.region == "us-west-2"
    assert provider.aws_profile == "default"
    assert provider.temperature == 0.0
    assert provider.extra_body == {"inferenceConfig": {"topK": 1}}


def test_bedrock_region_override_and_bad_temperature():
    config = Config.from_env({**LOCAL, **BEDROCK, "MODEL_BEDROCK_REGION": "eu-central-1"})
    assert config.model_providers[0].region == "eu-central-1"

    with pytest.raises(ConfigError, match="MODEL_BEDROCK_TEMPERATURE"):
        Config.from_env({**LOCAL, **BEDROCK, "MODEL_BEDROCK_TEMPERATURE": "cold"})


def test_wiring_builds_bedrock_without_touching_aws():
    """The client is created on first call, so a missing profile cannot stop the worker."""
    from worker.adapters.model_bedrock import BedrockConverseModel
    from worker.wiring import build

    env = {**BEDROCK, "MODEL_BEDROCK_AWS_PROFILE": "no-such-profile", "QUEUE_BACKEND": "memory"}
    app = build(Config.from_env(env))

    assert isinstance(app.deps.model._providers["bedrock"], BedrockConverseModel)


GITHUB = {
    "TOOLS_PROVIDER": "local",
    "GITHUB_TOKEN": "github_pat_secret",
    "GITHUB_REPOS": "octocat/Hello-World, octocat/Spoon-Knife",
}


def test_local_github_tools_from_env():
    config = Config.from_env({**LOCAL, **GITHUB})

    assert config.tools_provider == "local"
    assert config.github_repos == ("octocat/Hello-World", "octocat/Spoon-Knife")
    assert config.github_api_url == "https://api.github.com"
    assert "github_pat_secret" not in repr(config)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"GITHUB_TOKEN": ""}, "GITHUB_TOKEN is required"),
        ({"GITHUB_REPOS": ""}, "GITHUB_REPOS"),
        ({"GITHUB_REPOS": "just-a-name"}, "expected owner/name"),
    ],
)
def test_local_github_tools_need_token_and_repos(changes, expected):
    with pytest.raises(ConfigError, match=expected) as e:
        Config.from_env({**LOCAL, **GITHUB, **changes})
    assert "github_pat_secret" not in str(e.value)


def test_wiring_builds_github_tools():
    from worker.adapters.tools_local import GitHubTools
    from worker.wiring import build

    app = build(Config.from_env({**GITHUB, "QUEUE_BACKEND": "memory"}))

    assert isinstance(app.deps.tools, GitHubTools)


def test_mcp_tools_need_the_gateway_url_and_no_github_token():
    with pytest.raises(ConfigError, match="TOOL_GATEWAY_URL is required"):
        Config.from_env({**LOCAL, "TOOLS_PROVIDER": "mcp"})

    config = Config.from_env(
        {**LOCAL, "TOOLS_PROVIDER": "mcp", "TOOL_GATEWAY_URL": " http://gateway:8090 "}
    )
    assert config.tool_gateway_url == "http://gateway:8090"
    assert config.github_token is None


def test_wiring_builds_gateway_tools():
    from worker.adapters.tools_mcp import GatewayTools
    from worker.wiring import build

    env = {"TOOLS_PROVIDER": "mcp", "TOOL_GATEWAY_URL": "http://gateway:8090"}
    app = build(Config.from_env({**env, "QUEUE_BACKEND": "memory"}))

    assert isinstance(app.deps.tools, GatewayTools)


def test_metrics_off_unless_a_namespace_is_given():
    assert Config.from_env(LOCAL).metrics_namespace is None
    assert Config.from_env({**LOCAL, "METRICS_NAMESPACE": " "}).metrics_namespace is None
    assert (
        Config.from_env({**LOCAL, "METRICS_NAMESPACE": "AgentPlatform/dev"}).metrics_namespace
        == "AgentPlatform/dev"
    )


@pytest.mark.parametrize("namespace", ["AWS/Lambda", "bad,name", "x" * 256, "émoji"])
def test_bad_metrics_namespace(namespace):
    with pytest.raises(ConfigError, match="METRICS_NAMESPACE"):
        Config.from_env({**LOCAL, "METRICS_NAMESPACE": namespace})


def test_wiring_picks_metrics_from_the_namespace():
    from worker.adapters.metrics_emf import EmfMetrics
    from worker.core.metrics import NoopMetrics
    from worker.wiring import build

    env = {"QUEUE_BACKEND": "memory", "STATE_BACKEND": "memory"}
    assert isinstance(build(Config.from_env(env)).deps.metrics, NoopMetrics)
    with_namespace = build(Config.from_env({**env, "METRICS_NAMESPACE": "AgentPlatform/dev"}))
    assert isinstance(with_namespace.deps.metrics, EmfMetrics)
