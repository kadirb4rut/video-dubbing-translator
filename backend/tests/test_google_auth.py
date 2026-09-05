from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from app import main as main_module
from app.db import SessionLocal
from app.google_auth import GoogleAuthError, GoogleIdentity, exchange_code
from app.main import app
from app.models import AuthIdentity, User
from fastapi.testclient import TestClient
from sqlalchemy import select

GOOGLE_REDIRECT_URI = "https://app.example.com/api/auth/google/callback"


def configure_google(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(
            main_module.settings,
            frontend_origin="https://app.example.com",
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri=GOOGLE_REDIRECT_URI,
        ),
    )


def start_google(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/auth/google/start", follow_redirects=False)
    assert response.status_code == 303, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["state"][0], query["nonce"][0]


def test_google_oauth_creates_user_and_reuses_verified_identity(monkeypatch):
    configure_google(monkeypatch)
    with TestClient(app) as client:
        state, nonce = start_google(client)
        identity = GoogleIdentity("google-subject-1", "new-google@example.com", "Google User", True, nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: identity)

        callback = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert callback.status_code == 303
        assert client.get("/api/auth/me").json()["user"]["email"] == "new-google@example.com"

        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "new-google@example.com"))
            linked = db.scalars(select(AuthIdentity).where(AuthIdentity.user_id == user.id)).all()
            assert len(linked) == 1
            assert linked[0].provider_subject == "google-subject-1"

        assert client.post("/api/auth/logout").status_code == 200
        state, nonce = start_google(client)
        identity = GoogleIdentity("google-subject-1", "new-google@example.com", "Google User", True, nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: identity)
        callback = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert callback.status_code == 303
        assert client.get("/api/auth/me").status_code == 200


def test_google_oauth_links_existing_email_without_replacing_password(monkeypatch):
    configure_google(monkeypatch)
    with TestClient(app) as client:
        signup = client.post(
            "/api/auth/signup",
            json={"email": "existing@example.com", "password": "a-strong-password-123", "display_name": "Existing"},
        )
        assert signup.status_code == 200
        assert client.post("/api/auth/logout").status_code == 200

        state, nonce = start_google(client)
        identity = GoogleIdentity("google-subject-existing", "existing@example.com", "Existing Google", True, nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: identity)
        callback = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert callback.status_code == 303
        assert client.get("/api/auth/me").json()["user"]["display_name"] == "Existing"
        assert client.post("/api/auth/logout").status_code == 200

        password_login = client.post("/api/auth/login", json={"email": "existing@example.com", "password": "a-strong-password-123"})
        assert password_login.status_code == 200
        with SessionLocal() as db:
            assert db.scalar(select(AuthIdentity).where(AuthIdentity.provider_subject == "google-subject-existing")) is not None


def test_google_oauth_rejects_replay_nonce_and_provider_email_conflict(monkeypatch):
    configure_google(monkeypatch)
    with TestClient(app) as client:
        state, _ = start_google(client)
        wrong_nonce = GoogleIdentity("google-subject-replay", "replay@example.com", "Replay", True, "wrong-nonce")
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: wrong_nonce)
        callback = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert "auth_error=google_identity_invalid" in callback.headers["location"]
        replay = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert "auth_error=google_invalid_state" in replay.headers["location"]

        first_state, first_nonce = start_google(client)
        first = GoogleIdentity("google-subject-conflict-a", "conflict@example.com", "Conflict A", True, first_nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: first)
        assert client.get("/api/auth/google/callback", params={"code": "test-code", "state": first_state}, follow_redirects=False).status_code == 303
        assert client.post("/api/auth/logout").status_code == 200

        second_state, second_nonce = start_google(client)
        second = GoogleIdentity("google-subject-conflict-b", "conflict@example.com", "Conflict B", True, second_nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: second)
        conflict = client.get("/api/auth/google/callback", params={"code": "test-code", "state": second_state}, follow_redirects=False)
        assert "auth_error=google_identity_conflict" in conflict.headers["location"]
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.email == "conflict@example.com")) is not None
            assert db.scalar(select(AuthIdentity).where(AuthIdentity.provider_subject == "google-subject-conflict-b")) is None


def test_google_oauth_invalid_state_cancel_and_unverified_identity(monkeypatch):
    configure_google(monkeypatch)
    with TestClient(app) as client:
        state, nonce = start_google(client)
        with TestClient(app) as other_client:
            cross_browser = other_client.get(
                "/api/auth/google/callback",
                params={"code": "test-code", "state": state},
                follow_redirects=False,
            )
        assert "auth_error=google_invalid_state" in cross_browser.headers["location"]
        identity = GoogleIdentity("google-subject-cookie", "cookie@example.com", "Cookie", True, nonce)
        monkeypatch.setattr(main_module, "exchange_code", lambda config, *, code: identity)
        assert client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False).status_code == 303
        assert client.post("/api/auth/logout").status_code == 200

        missing = client.get("/api/auth/google/callback", params={"code": "test-code"}, follow_redirects=False)
        assert "auth_error=google_invalid_callback" in missing.headers["location"]

        state, _ = start_google(client)
        cancelled = client.get("/api/auth/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
        assert "auth_error=google_cancelled" in cancelled.headers["location"]
        reused = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert "auth_error=google_invalid_state" in reused.headers["location"]

        state, _ = start_google(client)

        def reject_unverified(config, *, code):
            raise GoogleAuthError("unverified")

        monkeypatch.setattr(main_module, "exchange_code", reject_unverified)
        invalid = client.get("/api/auth/google/callback", params={"code": "test-code", "state": state}, follow_redirects=False)
        assert "auth_error=google_identity_invalid" in invalid.headers["location"]
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.email == "unverified@example.com")) is None


def test_google_oauth_is_disabled_without_secret(monkeypatch):
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, google_client_id=None, google_client_secret=None, google_redirect_uri=None))
    with TestClient(app) as client:
        assert client.get("/api/auth/google/config").json() == {"enabled": False}
        assert client.get("/api/auth/google/start").status_code == 503


def test_google_token_exchange_normalizes_verified_identity_and_rejects_unverified_email(monkeypatch):
    from google.oauth2 import id_token

    config = SimpleNamespace(google_client_id="client-id", google_client_secret="client-secret", google_redirect_uri=GOOGLE_REDIRECT_URI)
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "google-subject-unit",
        "email": "  Verified@Example.COM ",
        "email_verified": True,
        "name": "Verified User",
        "nonce": "expected-nonce",
    }

    class TokenResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id_token": "signed-token"}

    monkeypatch.setattr("app.google_auth.httpx.post", lambda *args, **kwargs: TokenResponse())
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, request, audience: claims)
    identity = exchange_code(config, code="authorization-code")
    assert identity.email == "verified@example.com"
    assert identity.email_verified is True
    assert identity.nonce == "expected-nonce"

    claims["email_verified"] = False
    with pytest.raises(GoogleAuthError, match="verified"):
        exchange_code(config, code="authorization-code")
