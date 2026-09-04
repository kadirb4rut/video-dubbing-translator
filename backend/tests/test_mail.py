from app.mail import SESMailProvider


class FakeSesClient:
    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)


def test_ses_provider_sends_plain_text_messages_through_task_role_client():
    client = FakeSesClient()
    provider = SESMailProvider(client)

    provider.send_password_reset("person@example.com", "one-time-token")
    provider.send_job_update("person@example.com", job_id="job-1", operation="transcription", state="completed")

    assert len(client.calls) == 2
    assert client.calls[0]["Destination"] == {"ToAddresses": ["person@example.com"]}
    assert "one-time-token" in client.calls[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "job-1" in client.calls[1]["Content"]["Simple"]["Body"]["Text"]["Data"]
