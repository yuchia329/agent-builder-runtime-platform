"""Worker settings, read from environment variables (docs/architecture.md → Configuration).

Locally the values come from the repo-root .env; in AWS from the ECS task definition.
AWS_REGION, credentials and AWS_ENDPOINT_URL_SQS are read by boto3 itself.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

QUEUE_BACKENDS = ("sqs", "memory")
STATE_BACKENDS = ("dynamodb", "memory")
MODEL_PROTOCOLS = ("openai", "bedrock")
TOOLS_PROVIDERS = ("fake", "local", "mcp")
PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # matches the spec's model pattern
BUILT_IN_PROVIDERS = ("stub",)
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class ModelProviderConfig:
    """One entry of MODEL_PROVIDERS. Specs name it as the part of `model` before the '/'."""

    name: str
    protocol: str
    base_url: str = ""  # openai only
    api_key: str | None = field(default=None, repr=False)  # secret: never logged
    max_tokens: int = 4096
    extra_body: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None  # bedrock only (Nova tool use wants 0)
    region: str | None = None  # bedrock only; defaults to AWS_REGION
    aws_profile: str | None = None  # bedrock only; named profile instead of AWS_* env keys


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    queue_backend: str
    run_queue_url: str | None
    state_backend: str
    state_table: str
    control_plane_url: str
    model_providers: tuple[ModelProviderConfig, ...]
    tools_provider: str
    github_token: str | None = field(repr=False)  # secret: never logged
    github_repos: tuple[str, ...]
    github_api_url: str
    tool_gateway_url: str | None
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
            state_backend=choice("STATE_BACKEND", STATE_BACKENDS, "dynamodb"),
            state_table=env.get("STATE_TABLE") or "run-state",
            control_plane_url=env.get("CONTROL_PLANE_URL", "http://localhost:8080"),
            model_providers=_model_providers(env, errors),
            tools_provider=choice("TOOLS_PROVIDER", TOOLS_PROVIDERS, "fake"),
            github_token=env.get("GITHUB_TOKEN") or None,
            github_repos=tuple(
                r.strip() for r in env.get("GITHUB_REPOS", "").split(",") if r.strip()
            ),
            github_api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
            tool_gateway_url=env.get("TOOL_GATEWAY_URL", "").strip() or None,
            aws_region=env.get("AWS_REGION", "us-east-1"),
            concurrency=positive_int("WORKER_CONCURRENCY", 4),
            visibility_seconds=positive_int("RUN_VISIBILITY_SECONDS", 60),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )
        if config.tools_provider == "local":
            if not config.github_token:
                errors.append("GITHUB_TOKEN is required when TOOLS_PROVIDER=local")
            if not config.github_repos:
                errors.append("GITHUB_REPOS (owner/name,...) is required when TOOLS_PROVIDER=local")
            bad = [r for r in config.github_repos if not REPO.match(r)]
            if bad:
                errors.append(f"GITHUB_REPOS: expected owner/name, got {', '.join(bad)}")
        if config.tools_provider == "mcp" and not config.tool_gateway_url:
            errors.append("TOOL_GATEWAY_URL is required when TOOLS_PROVIDER=mcp")
        if "MODEL_PROVIDER" in env:
            errors.append("MODEL_PROVIDER was replaced by MODEL_PROVIDERS (ADR 0010); remove it")
        if config.queue_backend == "sqs" and not config.run_queue_url:
            errors.append("RUN_QUEUE_URL is required when QUEUE_BACKEND=sqs")
        if errors:
            raise ConfigError("Invalid worker configuration:\n  " + "\n  ".join(errors))
        return config


def _model_providers(env: Mapping[str, str], errors: list[str]) -> tuple[ModelProviderConfig, ...]:
    """MODEL_PROVIDERS=minimax,local plus MODEL_<NAME>_* per name. `stub` is always built in."""
    names = [n.strip() for n in env.get("MODEL_PROVIDERS", "").split(",") if n.strip()]
    providers = []
    for name in names:
        prefix = f"MODEL_{name.upper()}_"
        if not PROVIDER_NAME.match(name):
            errors.append(f"MODEL_PROVIDERS: {name!r} must be lowercase letters, digits and _")
            continue
        if name in BUILT_IN_PROVIDERS:
            errors.append(f"MODEL_PROVIDERS: {name!r} is built in; do not list it")
            continue
        protocol = env.get(prefix + "PROTOCOL", "")
        if protocol not in MODEL_PROTOCOLS:
            errors.append(
                f"{prefix}PROTOCOL={protocol!r}: expected one of {', '.join(MODEL_PROTOCOLS)}"
            )
        base_url = env.get(prefix + "BASE_URL", "")
        if protocol == "openai" and not base_url:
            errors.append(f"{prefix}BASE_URL is required")
        max_tokens_raw = env.get(prefix + "MAX_TOKENS", "4096")
        if not max_tokens_raw.isdigit() or int(max_tokens_raw) < 1:
            errors.append(f"{prefix}MAX_TOKENS={max_tokens_raw!r}: expected a positive integer")
            max_tokens_raw = "4096"
        temperature: float | None = None
        if raw_temp := env.get(prefix + "TEMPERATURE"):
            try:
                temperature = float(raw_temp)
            except ValueError:
                errors.append(f"{prefix}TEMPERATURE={raw_temp!r}: expected a number")
        extra_body: Any = {}
        if raw := env.get(prefix + "EXTRA_BODY"):
            try:
                extra_body = json.loads(raw)
            except json.JSONDecodeError:
                extra_body = None
            if not isinstance(extra_body, dict):
                errors.append(f"{prefix}EXTRA_BODY must be a JSON object")
                extra_body = {}
        providers.append(
            ModelProviderConfig(
                name=name,
                protocol=protocol,
                base_url=base_url,
                api_key=env.get(prefix + "API_KEY") or None,
                max_tokens=int(max_tokens_raw),
                extra_body=extra_body,
                temperature=temperature,
                region=env.get(prefix + "REGION") or env.get("AWS_REGION") or None,
                aws_profile=env.get(prefix + "AWS_PROFILE") or None,
            )
        )
    return tuple(providers)
