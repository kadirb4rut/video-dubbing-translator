from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.main import app
from app.models import Job, JobArtifact, User
from app.worker import JobWorker
from conftest import TEST_ROOT
from fastapi.testclient import TestClient


def fixture_audio() -> Path:
    path = TEST_ROOT / "fixture.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5", "-ac", "1", "-ar", "16000", str(path)], check=True)
    return path


def fixture_video() -> Path:
    path = TEST_ROOT / "fixture.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x24364b:s=320x240:r=24:d=3.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
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


def test_successful_transcription_worker_lifecycle(monkeypatch):
    def fake_transcribe(provider, audio_path, *, language=None):
        provider.detected_language = "en"
        return [{"start": 0.0, "end": 1.2, "text": "Hello world"}]

    monkeypatch.setattr("app.providers_real.WhisperTranscriptionProvider.transcribe", fake_transcribe)
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "worker-success@example.com", "password": "a-strong-password-123", "display_name": "Worker"})
        assert signup.status_code == 200
        source = fixture_audio()
        uploaded = client.post("/api/media/upload", files={"upload": ("source.wav", source.read_bytes(), "audio/wav")})
        assert uploaded.status_code == 200
        created = client.post("/api/jobs", headers={"Idempotency-Key": "worker-success"}, json={"media_asset_id": uploaded.json()["id"], "operation": "transcription"})
        assert created.status_code == 200, created.text

        assert JobWorker().run_once() is True
        detail = client.get(f"/api/jobs/{created.json()['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["state"] == "completed"
        assert body["actual_credits"] == body["reserved_credits"] == 1
        assert {artifact["name"] for artifact in body["artifacts"]} == {"srt", "vtt", "txt"}
        txt = next(artifact for artifact in body["artifacts"] if artifact["name"] == "txt")
        assert client.get(f"/api/jobs/{body['id']}/artifacts/{txt['id']}/preview").json()["text"] == "Hello world\n"
        usage = client.get("/api/usage")
        assert usage.status_code == 200
        assert usage.json()[0]["operation"] == "transcription"
        assert usage.json()[0]["actual_credits"] == 1
        assert client.get("/api/credits").json()["balance"] == 29


def test_worker_operations_produce_decodable_artifacts(monkeypatch):
    def fake_transcribe(provider, audio_path, *, language=None):
        provider.detected_language = "en"
        return [{"start": 0.0, "end": 1.2, "text": "Hello world"}]

    def fake_separate(provider, audio_path, *, stems, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        names = ["vocals", "instrumental"] if stems == 2 else ["vocals", "drums", "bass", "other"]
        outputs = {}
        for name in names:
            output = output_dir / f"{name}.wav"
            shutil.copy2(audio_path, output)
            outputs[name] = output
        return outputs

    def fake_enhance(provider, audio_path, *, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(audio_path, output_path)
        return output_path

    def fake_synthesize(provider, text, *, reference_voice, language, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference_voice, output_path)
        return output_path

    class FakeTranslation:
        name = "fixture-translation"

        def translate(self, segments, *, source, target):
            assert source == "en"
            return [{**segment, "text": f"Hola: {segment['text']}"} for segment in segments]

    monkeypatch.setattr("app.providers_real.WhisperTranscriptionProvider.transcribe", fake_transcribe)
    monkeypatch.setattr("app.providers_real.DemucsStemSeparationProvider.separate", fake_separate)
    monkeypatch.setattr("app.providers_real.DeepFilterNetNoiseProvider.enhance", fake_enhance)
    monkeypatch.setattr("app.providers_real.ChatterboxMultilingualVoiceProvider.synthesize", fake_synthesize)
    monkeypatch.setattr("app.worker.translation_provider", lambda: FakeTranslation())

    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "all-tools@example.com", "password": "a-strong-password-123", "display_name": "All tools"})
        assert signup.status_code == 200
        source_audio = fixture_audio()
        uploaded_audio = client.post("/api/media/upload", files={"upload": ("fixture.wav", source_audio.read_bytes(), "audio/wav")})
        assert uploaded_audio.status_code == 200
        audio_id = uploaded_audio.json()["id"]

        subtitles = client.post("/api/jobs", json={"media_asset_id": audio_id, "operation": "subtitle_translation", "target_language": "es"})
        assert subtitles.status_code == 200
        assert JobWorker().run_once() is True
        subtitle_detail = client.get(f"/api/jobs/{subtitles.json()['id']}").json()
        assert subtitle_detail["state"] == "completed"
        assert {artifact["name"] for artifact in subtitle_detail["artifacts"]} == {"srt", "vtt", "txt"}

        stems = client.post("/api/jobs", json={"media_asset_id": audio_id, "operation": "stems", "stems": 4})
        assert stems.status_code == 200
        assert JobWorker().run_once() is True
        stems_detail = client.get(f"/api/jobs/{stems.json()['id']}").json()
        assert stems_detail["state"] == "completed"
        assert {artifact["name"] for artifact in stems_detail["artifacts"]} == {"stems_zip", "vocals", "drums", "bass", "other"}
        vocals = next(artifact for artifact in stems_detail["artifacts"] if artifact["name"] == "vocals")
        assert client.get(f"/api/jobs/{stems_detail['id']}/artifacts/{vocals['id']}/download").status_code == 200

        noise = client.post("/api/jobs", json={"media_asset_id": audio_id, "operation": "noise"})
        assert noise.status_code == 200
        assert JobWorker().run_once() is True
        noise_detail = client.get(f"/api/jobs/{noise.json()['id']}").json()
        assert noise_detail["state"] == "completed"
        assert next(artifact for artifact in noise_detail["artifacts"] if artifact["name"] == "enhanced_audio")["content_type"] == "audio/wav"

        video = fixture_video()
        uploaded_video = client.post("/api/media/upload", files={"upload": ("fixture.mp4", video.read_bytes(), "video/mp4")})
        assert uploaded_video.status_code == 200
        voice = client.post("/api/voices", data={"name": "Fixture voice", "declaration": "I own or am authorized to use this voice.", "authorized": "true"}, files={"upload": ("reference.wav", source_audio.read_bytes(), "audio/wav")})
        assert voice.status_code == 200
        voice_id = voice.json()["id"]

        tts = client.post(f"/api/voices/{voice_id}/synthesize", json={"text": "Hello world", "language": "es"})
        assert tts.status_code == 200
        assert JobWorker().run_once() is True
        tts_detail = client.get(f"/api/jobs/{tts.json()['id']}").json()
        assert tts_detail["state"] == "completed"
        assert next(artifact for artifact in tts_detail["artifacts"] if artifact["name"] == "speech")["content_type"] == "audio/wav"

        dubbing = client.post("/api/jobs", json={"media_asset_id": uploaded_video.json()["id"], "operation": "dubbing", "target_language": "es", "voice_profile_id": voice_id, "preserve_voice": True, "keep_background": True})
        assert dubbing.status_code == 200, dubbing.text
        assert JobWorker().run_once() is True
        dubbing_detail = client.get(f"/api/jobs/{dubbing.json()['id']}").json()
        assert dubbing_detail["state"] == "completed"
        dubbed = next(artifact for artifact in dubbing_detail["artifacts"] if artifact["name"] == "dubbed_video")
        assert dubbed["content_type"] == "video/mp4"
        assert client.get(f"/api/jobs/{dubbing_detail['id']}/artifacts/{dubbed['id']}/download").status_code == 200
        dubbing_usage = next(entry for entry in client.get("/api/usage").json() if entry["operation"] == "dubbing")
        assert dubbing_usage["output_duration_seconds"] is not None


def test_unauthenticated_requests_are_rejected():
    with TestClient(app) as client:
        response = client.get("/api/credits")
        assert response.status_code == 401


def test_billing_fails_closed_until_stripe_is_configured():
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "billing-fail-closed@example.com", "password": "a-strong-password-123", "display_name": "Billing"})
        assert signup.status_code == 200
        summary = client.get("/api/billing")
        assert summary.status_code == 200
        assert summary.json()["provider"] == "disabled"
        assert summary.json()["checkout_enabled"] is False
        assert client.post("/api/billing/checkout", json={"kind": "credits", "key": "starter"}).status_code == 503
        assert client.post("/api/billing/portal").status_code == 503
        assert client.post("/api/billing/webhook", content=b"{}").status_code == 400
        assert client.post("/api/billing/webhook", headers={"Stripe-Signature": "test"}, content=b"{}").status_code == 503


def test_password_reset_token_rotates_password_and_revokes_sessions():
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "reset@example.com", "password": "old-password-123", "display_name": "Reset"})
        assert signup.status_code == 200
        requested = client.post("/api/auth/password-reset/request", json={"email": "reset@example.com"})
        assert requested.status_code == 200
        token = requested.json()["dev_token"]
        confirmed = client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "new-password-123"})
        assert confirmed.status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        login = client.post("/api/auth/login", json={"email": "reset@example.com", "password": "new-password-123"})
        assert login.status_code == 200


def test_user_cancellation_finalizes_reserved_credits_and_does_not_run_job():
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "cancel@example.com", "password": "a-strong-password-123", "display_name": "Cancel"})
        assert signup.status_code == 200
        source = fixture_audio()
        uploaded = client.post("/api/media/upload", files={"upload": ("cancel.wav", source.read_bytes(), "audio/wav")})
        assert uploaded.status_code == 200
        created = client.post("/api/jobs", headers={"Idempotency-Key": "cancel-job"}, json={"media_asset_id": uploaded.json()["id"], "operation": "transcription"})
        assert created.status_code == 200, created.text
        cancelled = client.post(f"/api/jobs/{created.json()['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["actual_credits"] == cancelled.json()["reserved_credits"]
        assert client.get("/api/credits").json()["balance"] == 29


def test_text_artifact_can_be_previewed_and_saved_by_owner():
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "editor@example.com", "password": "a-strong-password-123", "display_name": "Editor"})
        assert signup.status_code == 200
        from io import BytesIO

        from app.db import SessionLocal
        from app.storage import object_store

        with SessionLocal() as db:
            user = db.query(User).filter_by(email="editor@example.com").one()
            job = Job(user_id=user.id, operation="transcription", state="completed", idempotency_key="editor-job", estimate_credits=1, reserved_credits=1, actual_credits=1)
            db.add(job)
            db.flush()
            key = f"users/{user.id}/outputs/{job.id}/transcript.txt"
            object_store().put(key, BytesIO(b"before\n"), content_type="text/plain")
            artifact = JobArtifact(job_id=job.id, artifact_name="txt", object_key=key, filename="transcript.txt", content_type="text/plain", size_bytes=7)
            db.add(artifact)
            db.commit()
            artifact_id = artifact.id
            job_id = job.id
        preview = client.get(f"/api/jobs/{job_id}/artifacts/{artifact_id}/preview")
        assert preview.json()["text"] == "before\n"
        saved = client.patch(f"/api/jobs/{job_id}/artifacts/{artifact_id}", json={"text": "after\n"})
        assert saved.status_code == 200
        assert saved.json()["size_bytes"] == 6
        assert client.get(f"/api/jobs/{job_id}/artifacts/{artifact_id}/preview").json()["text"] == "after\n"


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
