"""Worker settings, read from environment variables (docs/architecture.md → Configuration).

Locally the values come from the repo-root .env; in AWS from the ECS task definition.
AWS_REGION, credentials and AWS_ENDPOINT_URL_SQS are read by boto3 itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

QUEUE_BACKENDS = ("sqs", "memory")
MODEL_PROVIDERS = ("stub",)  # bedrock in M1
TOOLS_PROVIDERS = ("fake",)  # local in M1, mcp in M4


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    queue_backend: str
    run_queue_url: str | None
    control_plane_url: str
    model_provider: str
    tools_provider: str
    aws_region: str
    concurrency: int
    visibility_seconds: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Config:
        errors: list[str] = []

        def choice(name: str, allowed: tuple[str, ...], default: str) -> str:
            value = env.get(name, default)
            if value not in allowed:
                errors.append(f"{name}={value!r}: expected one of {', '.join(allowed)}")
            return value

        def positive_int(name: str, default: int) -> int:
            raw = env.get(name, str(default))
            if not raw.isdigit() or int(raw) < 1:
                errors.append(f"{name}={raw!r}: expected a positive integer")
                return default
            return int(raw)

        config = cls(
            queue_backend=choice("QUEUE_BACKEND", QUEUE_BACKENDS, "sqs"),
            run_queue_url=env.get("RUN_QUEUE_URL") or None,
            control_plane_url=env.get("CONTROL_PLANE_URL", "http://localhost:8080"),
            model_provider=choice("MODEL_PROVIDER", MODEL_PROVIDERS, "stub"),
            tools_provider=choice("TOOLS_PROVIDER", TOOLS_PROVIDERS, "fake"),
            aws_region=env.get("AWS_REGION", "us-east-1"),
            concurrency=positive_int("WORKER_CONCURRENCY", 4),
            visibility_seconds=positive_int("RUN_VISIBILITY_SECONDS", 60),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )
        if config.queue_backend == "sqs" and not config.run_queue_url:
            errors.append("RUN_QUEUE_URL is required when QUEUE_BACKEND=sqs")
        if errors:
            raise ConfigError("Invalid worker configuration:\n  " + "\n  ".join(errors))
        return config
