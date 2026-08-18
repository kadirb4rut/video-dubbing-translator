from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="lingowave-api-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["LOCAL_STORAGE_DIR"] = str(TEST_ROOT / "objects")

from fastapi.testclient import TestClient

from app.main import app
from app.worker import JobWorker


def fixture_audio() -> Path:
    path = TEST_ROOT / "fixture.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5", "-ac", "1", "-ar", "16000", str(path)], check=True)
    return path


def test_signup_upload_estimate_and_idempotent_job():
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "owner@example.com", "password": "a-strong-password-123", "display_name": "Owner"})
        assert signup.status_code == 200, signup.text
        assert signup.json()["credits"] == 30

        source = fixture_audio()
        uploaded = client.post("/api/media/upload", files={"upload": ("fixture.wav", source.read_bytes(), "audio/wav")})
        assert uploaded.status_code == 200, uploaded.text
        asset = uploaded.json()
        assert asset["duration_seconds"] > 0

        voice = client.post("/api/voices", data={"name": "Owner voice", "declaration": "I own or am authorized to use this voice.", "authorized": "true"}, files={"upload": ("reference.wav", source.read_bytes(), "audio/wav")})
        assert voice.status_code == 200, voice.text
        assert client.delete(f"/api/voices/{voice.json()['id']}").json()["deleted"] is True

        estimate = client.post("/api/jobs/estimate", json={"media_asset_id": asset["id"], "operation": "transcription"})
        assert estimate.status_code == 200
        assert estimate.json()["credits"] == 1
        assert client.post("/api/jobs/estimate", json={"media_asset_id": asset["id"], "operation": "dubbing"}).status_code == 422

        body = {"media_asset_id": asset["id"], "operation": "transcription"}
        first = client.post("/api/jobs", headers={"Idempotency-Key": "same-job"}, json=body)
        second = client.post("/api/jobs", headers={"Idempotency-Key": "same-job"}, json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        worker = JobWorker()
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is True
        failed = client.get(f"/api/jobs/{first.json()['id']}")
        assert failed.json()["state"] == "failed"
        assert failed.json()["error_code"] == "PROVIDER_FAILURE"
        credits = client.get("/api/credits")
        assert credits.json()["balance"] == 30


def test_unauthenticated_requests_are_rejected():
    with TestClient(app) as client:
        response = client.get("/api/credits")
        assert response.status_code == 401


def test_projects_password_reset_account_and_abuse_flow():
    with TestClient(app) as client:
        email = f"account-{next(tempfile._get_candidate_names())}@example.com"
        signup = client.post("/api/auth/signup", json={"email": email, "password": "a-strong-password-123", "display_name": "Account"})
        assert signup.status_code == 200
        project = client.post("/api/projects", json={"name": "Acceptance project"})
        assert project.status_code == 200
        assert client.get("/api/projects").json()[0]["name"] == "Acceptance project"
        reset = client.post("/api/auth/password-reset/request", json={"email": email})
        assert reset.status_code == 200
        assert reset.json().get("dev_token")
        confirmed = client.post("/api/auth/password-reset/confirm", json={"token": reset.json()["dev_token"], "password": "a-new-strong-password-123"})
        assert confirmed.status_code == 200
        assert client.post("/api/auth/login", json={"email": email, "password": "a-new-strong-password-123"}).status_code == 200
        report = client.post("/api/abuse/report", json={"description": "Test report for the acceptance harness."})
        assert report.status_code == 200
        assert client.patch("/api/account", json={"display_name": "Updated Account"}).json()["display_name"] == "Updated Account"
        deleted = client.delete("/api/account")
        assert deleted.status_code == 200
        assert client.get("/api/account").status_code == 401
