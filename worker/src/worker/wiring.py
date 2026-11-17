"""Builds the adapters that config selects. The only module that knows every adapter."""

from __future__ import annotations

from dataclasses import dataclass

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_http import HttpControlPlane, default_signer
from worker.adapters.model_stub import StubModel, demo_turns
from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.queue_sqs import SqsRunQueue
from worker.adapters.tools_fake import issue_triage_tools
from worker.config import Config
from worker.core.handle_run import Deps
from worker.ports.queue import RunQueue


@dataclass(frozen=True)
class App:
    queue: RunQueue
    deps: Deps


def build(config: Config) -> App:
    if config.queue_backend == "sqs":
        assert config.run_queue_url  # checked by Config
        queue: RunQueue = SqsRunQueue(
            config.run_queue_url, config.visibility_seconds, region=config.aws_region
        )
    else:
        queue = InMemoryRunQueue(config.visibility_seconds)

    control_plane = HttpControlPlane(config.control_plane_url, default_signer(config.aws_region))
    model = StubModel(demo_turns())  # MODEL_PROVIDER=stub is the only choice until M1
    tools = issue_triage_tools()  # TOOLS_PROVIDER=fake is the only choice until M1
    return App(queue, Deps(control_plane, model, tools, SystemClock()))
