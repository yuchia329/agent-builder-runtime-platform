"""RunQueue on Amazon SQS (ElasticMQ locally, via AWS_ENDPOINT_URL_SQS)."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from worker.core.errors import QueueError
from worker.ports.queue import QueueMessage

SQS_MAX_MESSAGES = 10
SQS_MAX_WAIT_SECONDS = 20


class SqsRunQueue:
    def __init__(
        self,
        queue_url: str,
        visibility_seconds: int = 60,
        client: Any = None,
        region: str | None = None,
    ) -> None:
        self._url = queue_url
        self._visibility = visibility_seconds
        # boto3 reads AWS_DEFAULT_REGION but not AWS_REGION, so the region is passed explicitly.
        self._sqs = client or boto3.client("sqs", region_name=region)

    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        try:
            response = self._sqs.receive_message(
                QueueUrl=self._url,
                MaxNumberOfMessages=max(1, min(max_messages, SQS_MAX_MESSAGES)),
                WaitTimeSeconds=min(wait_seconds, SQS_MAX_WAIT_SECONDS),
                VisibilityTimeout=self._visibility,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as e:
            raise QueueError(f"receive failed: {e}") from e
        return [
            QueueMessage(
                body=m["Body"],
                receipt=m["ReceiptHandle"],
                receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1)),
            )
            for m in response.get("Messages", [])
        ]

    def delete(self, message: QueueMessage) -> None:
        try:
            self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=message.receipt)
        except (BotoCoreError, ClientError) as e:
            raise QueueError(f"delete failed: {e}") from e

    def extend(self, message: QueueMessage, seconds: int) -> None:
        try:
            self._sqs.change_message_visibility(
                QueueUrl=self._url, ReceiptHandle=message.receipt, VisibilityTimeout=seconds
            )
        except (BotoCoreError, ClientError) as e:
            raise QueueError(f"extend failed: {e}") from e
