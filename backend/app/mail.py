from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from .config import settings


class MailProvider(Protocol):
    name: str

    def send_password_reset(self, recipient: str, token: str) -> None: ...

    def send_job_update(self, recipient: str, *, job_id: str, operation: str, state: str) -> None: ...


class DevelopmentMailSink:
    name = "development-mail-sink"

    def send_password_reset(self, recipient: str, token: str) -> None:
        logging.getLogger("lingowave.mail").info("password reset issued token_issued=true")

    def send_job_update(self, recipient: str, *, job_id: str, operation: str, state: str) -> None:
        logging.getLogger("lingowave.mail").info("job update job_id=%s operation=%s state=%s", job_id, operation, state)


class SMTPMailProvider:
    name = "smtp"

    def send_password_reset(self, recipient: str, token: str) -> None:
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is required for SMTP mail")
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = recipient
        message["Subject"] = "Reset your LingoWave password"
        message.set_content(f"Use this one-time LingoWave password reset token within 30 minutes:\n\n{token}\n")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password or "")
            client.send_message(message)

    def send_job_update(self, recipient: str, *, job_id: str, operation: str, state: str) -> None:
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is required for SMTP mail")
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = recipient
        message["Subject"] = f"LingoWave job {state}: {operation}"
        message.set_content(f"Your LingoWave {operation} job is {state}.\n\nJob ID: {job_id}\n")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password or "")
            client.send_message(message)


class SESMailProvider:
    name = "ses"

    def __init__(self, client=None):
        if client is not None:
            self.client = client
            return
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for SES mail") from exc
        self.client = boto3.client("sesv2", region_name=settings.s3_region)

    def _send(self, recipient: str, subject: str, body: str) -> None:
        self.client.send_email(
            FromEmailAddress=settings.mail_from,
            Destination={"ToAddresses": [recipient]},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
        )

    def send_password_reset(self, recipient: str, token: str) -> None:
        self._send(recipient, "Reset your LingoWave password", f"Use this one-time LingoWave password reset token within 30 minutes:\n\n{token}\n")

    def send_job_update(self, recipient: str, *, job_id: str, operation: str, state: str) -> None:
        self._send(recipient, f"LingoWave job {state}: {operation}", f"Your LingoWave {operation} job is {state}.\n\nJob ID: {job_id}\n")


def mail_provider() -> MailProvider:
    if settings.mail_provider == "smtp":
        return SMTPMailProvider()
    if settings.mail_provider == "ses":
        return SESMailProvider()
    if settings.dev_mail_sink:
        return DevelopmentMailSink()
    raise RuntimeError("No mail provider is configured")
