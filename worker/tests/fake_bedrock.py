"""A Bedrock Runtime client with no network: botocore's Stubber replays recorded Converse replies
from tests/fixtures/bedrock/ and checks every request against Bedrock's API model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3
from botocore.stub import Stubber

from worker.adapters.model_bedrock import BedrockConverseModel

FIXTURES = Path(__file__).parent / "fixtures" / "bedrock"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def stub_client() -> tuple[Any, Stubber]:
    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(client)
    stubber.activate()
    return client, stubber


def stubbed_bedrock(*replies: str | dict[str, Any], **kwargs: Any) -> BedrockConverseModel:
    """Adapter whose client answers with these fixtures (names) or reply dicts, in order."""
    client, stubber = stub_client()
    for reply in replies:
        stubber.add_response("converse", fixture(reply) if isinstance(reply, str) else reply)
    return BedrockConverseModel(client=client, **kwargs)
