"""메시지 큐 포트.

로컬은 elasticmq, 배포는 SQS — **같은 API 라서 코드가 바뀌지 않는다.** 그래서
localstack 이 아니라 elasticmq 를 쓴다(가볍고 SQS 호환).

boto3 는 동기 클라이언트다. 호출하는 쪽(릴레이)이 스레드에 태운다.
"""

import json
from typing import Any, Protocol

import boto3

from app.core.config import settings

IO_QUEUE = "io"
AI_QUEUE = "ai"


class Queue(Protocol):
    def send(self, queue_name: str, body: dict[str, Any]) -> None:
        """큐가 없으면 만든다(멱등). 본문은 JSON 문자열로 직렬화한다."""
        ...


class SqsQueue:
    def __init__(self) -> None:
        self._client = boto3.client(
            "sqs",
            endpoint_url=settings.sqs_endpoint_url or None,
            region_name=settings.sqs_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self._urls: dict[str, str] = {}

    def _url(self, queue_name: str) -> str:
        if queue_name not in self._urls:
            # CreateQueue 는 멱등이다. 이미 있으면 기존 URL 을 돌려준다.
            self._urls[queue_name] = str(
                self._client.create_queue(QueueName=queue_name)["QueueUrl"]
            )
        return self._urls[queue_name]

    def send(self, queue_name: str, body: dict[str, Any]) -> None:
        self._client.send_message(
            QueueUrl=self._url(queue_name), MessageBody=json.dumps(body, ensure_ascii=False)
        )
