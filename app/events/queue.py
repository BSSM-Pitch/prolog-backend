"""메시지 큐 포트.

로컬은 elasticmq, 배포는 SQS — **같은 API 라서 코드가 바뀌지 않는다.** 그래서
localstack 이 아니라 elasticmq 를 쓴다(가볍고 SQS 호환).

boto3 는 동기 클라이언트다. 호출하는 쪽(릴레이)이 스레드에 태운다.
"""

import json
from typing import Any, NamedTuple, Protocol

import boto3

from app.core.config import settings

IO_QUEUE = "io"
AI_QUEUE = "ai"
# 알림용. 잡과 같은 큐를 쓰면 notifier 와 extractor 가 서로의 메시지를 받아 지운다.
NOTIFY_QUEUE = "notify"
# 소비자가 지우지 못한(처리에 실패한) 메시지가 maxReceiveCount 번 받아지면 가는 곳.
DLQ_SUFFIX = "-dlq"


class Message(NamedTuple):
    receipt: str
    body: dict[str, Any]


class Queue(Protocol):
    def send(self, queue_name: str, body: dict[str, Any]) -> None:
        """큐가 없으면 만든다(멱등). 본문은 JSON 문자열로 직렬화한다."""
        ...

    def receive(self, queue_name: str, max_messages: int, wait_seconds: int) -> list[Message]:
        """롱 폴링으로 받는다. 처리한 메시지는 `delete` 로 지운다."""
        ...

    def delete(self, queue_name: str, receipt: str) -> None: ...


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
            url = str(self._client.create_queue(QueueName=queue_name)["QueueUrl"])
            if not queue_name.endswith(DLQ_SUFFIX):
                self._attach_dlq(url, queue_name)
            # 큐별 visibility timeout. 없는 큐(notify)는 브로커 기본값(30초)이다. 이미 있는 큐에도
            # 매번 건다(멱등) — create_queue 의 속성은 기존 큐에 반영되지 않는다.
            visibility = {
                AI_QUEUE: settings.queue_visibility_seconds_ai,
                IO_QUEUE: settings.queue_visibility_seconds_io,
            }.get(queue_name)
            if visibility is not None:
                self._client.set_queue_attributes(
                    QueueUrl=url, Attributes={"VisibilityTimeout": str(visibility)}
                )
            self._urls[queue_name] = url
        return self._urls[queue_name]

    def _attach_dlq(self, url: str, queue_name: str) -> None:
        """poison 메시지가 큐를 영원히 막지 않게 한다. 이미 있는 큐에도 매번 다시 건다(멱등)."""
        dlq_url = self._url(queue_name + DLQ_SUFFIX)
        arn = self._client.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        policy = {"deadLetterTargetArn": arn, "maxReceiveCount": settings.queue_max_receive_count}
        self._client.set_queue_attributes(
            QueueUrl=url, Attributes={"RedrivePolicy": json.dumps(policy)}
        )

    def send(self, queue_name: str, body: dict[str, Any]) -> None:
        self._client.send_message(
            QueueUrl=self._url(queue_name), MessageBody=json.dumps(body, ensure_ascii=False)
        )

    def receive(
        self, queue_name: str, max_messages: int = 10, wait_seconds: int = 5
    ) -> list[Message]:
        response = self._client.receive_message(
            QueueUrl=self._url(queue_name),
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
        )
        return [
            Message(receipt=m["ReceiptHandle"], body=json.loads(m["Body"]))
            for m in response.get("Messages", [])
        ]

    def delete(self, queue_name: str, receipt: str) -> None:
        self._client.delete_message(QueueUrl=self._url(queue_name), ReceiptHandle=receipt)
