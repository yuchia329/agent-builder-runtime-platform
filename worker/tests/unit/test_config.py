import pytest

from worker.config import Config, ConfigError

LOCAL = {"RUN_QUEUE_URL": "http://localhost:9324/000000000000/runs"}


def test_defaults_for_local_development():
    config = Config.from_env(LOCAL)

    assert config.queue_backend == "sqs"
    assert config.control_plane_url == "http://localhost:8080"
    assert config.model_provider == "stub"
    assert config.tools_provider == "fake"
    assert config.concurrency == 4
    assert config.visibility_seconds == 60


def test_sqs_needs_queue_url():
    with pytest.raises(ConfigError, match="RUN_QUEUE_URL"):
        Config.from_env({})


def test_memory_queue_needs_no_url():
    assert Config.from_env({"QUEUE_BACKEND": "memory"}).run_queue_url is None


def test_reports_every_bad_value_at_once():
    env = {**LOCAL, "MODEL_PROVIDER": "gpt", "WORKER_CONCURRENCY": "0", "QUEUE_BACKEND": "kafka"}

    with pytest.raises(ConfigError) as e:
        Config.from_env(env)

    for name in ("MODEL_PROVIDER", "WORKER_CONCURRENCY", "QUEUE_BACKEND"):
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
