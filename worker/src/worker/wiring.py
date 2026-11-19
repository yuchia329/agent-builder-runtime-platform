"""Builds the adapters that config selects. The only module that knows every adapter."""

from __future__ import annotations

from dataclasses import dataclass

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_http import HttpControlPlane, default_signer
from worker.adapters.model_bedrock import BedrockConverseModel
from worker.adapters.model_openai_compat import OpenAICompatModel
from worker.adapters.model_router import ModelRouter
from worker.adapters.model_stub import StubModel, demo_turns
from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.queue_sqs import SqsRunQueue
from worker.adapters.run_state_dynamodb import DynamoDbRunStateStore
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_fake import issue_triage_tools
from worker.adapters.tools_local import GitHubTools
from worker.adapters.tools_mcp import GatewayTools, SignedAuth
from worker.config import Config
from worker.core.handle_run import Deps
from worker.ports.model import Model
from worker.ports.queue import RunQueue
from worker.ports.run_state import RunStateStore
from worker.ports.tools import ToolClient


@dataclass(frozen=True)
class App:
    queue: RunQueue
    deps: Deps
    state: RunStateStore


def build(config: Config) -> App:
    if config.queue_backend == "sqs":
        assert config.run_queue_url  # checked by Config
        queue: RunQueue = SqsRunQueue(
            config.run_queue_url, config.visibility_seconds, region=config.aws_region
        )
    else:
        queue = InMemoryRunQueue(config.visibility_seconds)

    clock = SystemClock()
    state: RunStateStore
    if config.state_backend == "dynamodb":
        state = DynamoDbRunStateStore(config.state_table, clock, region=config.aws_region)
    else:  # one process only: leases in memory do not stop another worker
        state = InMemoryRunStateStore(clock)

    control_plane = HttpControlPlane(config.control_plane_url, default_signer(config.aws_region))
    providers: dict[str, Model] = {"stub": StubModel(demo_turns())}
    for p in config.model_providers:
        if p.protocol == "bedrock":
            providers[p.name] = BedrockConverseModel(
                region=p.region or config.aws_region,
                profile=p.aws_profile,
                max_tokens=p.max_tokens,
                temperature=p.temperature,
                extra_body=p.extra_body,
            )
        else:
            providers[p.name] = OpenAICompatModel(
                p.base_url, p.api_key, max_tokens=p.max_tokens, extra_body=p.extra_body
            )
    model = ModelRouter(providers)
    tools: ToolClient
    if config.tools_provider == "local":
        assert config.github_token  # checked by Config
        tools = GitHubTools(config.github_token, config.github_repos, api_url=config.github_api_url)
    elif config.tools_provider == "mcp":
        assert config.tool_gateway_url  # checked by Config
        auth = SignedAuth(default_signer(config.aws_region)) if config.tool_gateway_sigv4 else None
        tools = GatewayTools(config.tool_gateway_url, auth=auth)
    else:
        tools = issue_triage_tools()
    return App(queue, Deps(control_plane, model, tools, clock, state=state), state)
