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
    }

    with pytest.raises(ConfigError) as e:
        Config.from_env(env)

    for name in ("MODEL_PROVIDERS", "WORKER_CONCURRENCY", "QUEUE_BACKEND"):
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
