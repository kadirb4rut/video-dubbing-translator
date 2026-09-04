from __future__ import annotations

import json
import queue
from dataclasses import dataclass

from .config import settings


@dataclass(frozen=True)
class JobMessage:
    job_id: str
    operation: str
    receipt_handle: str | None = None


_local_queue: queue.Queue[JobMessage] = queue.Queue()


class LocalQueue:
    def send(self, message: JobMessage) -> None:
        _local_queue.put(message)

    def receive(self, timeout: float = 0.25) -> JobMessage | None:
        try:
            return _local_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def delete(self, message: JobMessage) -> None:
        _local_queue.task_done()

    def change_visibility(self, message: JobMessage, timeout: int) -> None:
        # The in-process queue has no visibility lease to extend.
        return None

    def stats(self) -> dict[str, int]:
        return {"visible": _local_queue.qsize(), "in_flight": 0}


class SQSQueue:
    def __init__(self):
        import boto3

        if not settings.sqs_queue_url:
            raise RuntimeError("SQS_QUEUE_URL is required for SQS queueing")
        self.url = settings.sqs_queue_url
        self.client = boto3.client("sqs", region_name=settings.s3_region, endpoint_url=settings.sqs_endpoint_url)

    def send(self, message: JobMessage) -> None:
        params = {"QueueUrl": self.url, "MessageBody": json.dumps(message.__dict__)}
        if self.url.endswith(".fifo"):
            params["MessageGroupId"] = "lingowave"
            params["MessageDeduplicationId"] = message.job_id
        self.client.send_message(**params)

    def receive(self, timeout: float = 0.25) -> JobMessage | None:
        response = self.client.receive_message(QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=min(20, max(0, int(timeout))), VisibilityTimeout=settings.sqs_visibility_timeout_seconds)
        messages = response.get("Messages", [])
        if not messages:
            return None
        body = json.loads(messages[0]["Body"])
        return JobMessage(body["job_id"], body["operation"], messages[0]["ReceiptHandle"])

    def delete(self, message: JobMessage) -> None:
        if message.receipt_handle:
            self.client.delete_message(QueueUrl=self.url, ReceiptHandle=message.receipt_handle)

    def change_visibility(self, message: JobMessage, timeout: int) -> None:
        if message.receipt_handle:
            self.client.change_message_visibility(QueueUrl=self.url, ReceiptHandle=message.receipt_handle, VisibilityTimeout=timeout)

    def stats(self) -> dict[str, int]:
        attributes = self.client.get_queue_attributes(QueueUrl=self.url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return {"visible": int(attributes.get("ApproximateNumberOfMessages", 0)), "in_flight": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0))}


def job_queue():
    return SQSQueue() if settings.sqs_queue_url else LocalQueue()
