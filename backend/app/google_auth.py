from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class GoogleAuthError(Exception):
    """Raised when Google OAuth/OIDC cannot produce a verified identity."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    display_name: str
    email_verified: bool
    nonce: str


def google_auth_configured(config) -> bool:
    return bool(config.google_client_id and config.google_client_secret and config.google_redirect_uri)


def authorization_url(config, *, state: str, nonce: str) -> str:
    if not google_auth_configured(config):
        raise GoogleAuthError("Google authentication is not configured")
    query = urlencode(
        {
            "client_id": config.google_client_id,
            "redirect_uri": config.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code(config, *, code: str) -> GoogleIdentity:
    if not google_auth_configured(config):
        raise GoogleAuthError("Google authentication is not configured")
    try:
        response = httpx.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "redirect_uri": config.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleAuthError("Google authorization code exchange failed") from exc

    id_token_value = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(id_token_value, str) or not id_token_value:
        raise GoogleAuthError("Google did not return an identity token")

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(id_token_value, Request(), config.google_client_id)
    except Exception as exc:  # Google auth exposes several provider-specific verification errors.
        raise GoogleAuthError("Google identity token verification failed") from exc

    if not isinstance(claims, dict):
        raise GoogleAuthError("Google identity claims are invalid")
    issuer = claims.get("iss")
    subject = claims.get("sub")
    email = claims.get("email")
    nonce = claims.get("nonce")
    if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
        raise GoogleAuthError("Google identity issuer is invalid")
    if not isinstance(subject, str) or not subject.strip():
        raise GoogleAuthError("Google identity subject is missing")
    if not isinstance(email, str) or not email.strip():
        raise GoogleAuthError("Google identity email is missing")
    if claims.get("email_verified") is not True:
        raise GoogleAuthError("Google email is not verified")
    if not isinstance(nonce, str) or not nonce:
        raise GoogleAuthError("Google identity nonce is missing")

    normalized_email = email.strip().lower()
    display_name = str(claims.get("name") or normalized_email.split("@", 1)[0]).strip()[:120]
    return GoogleIdentity(
        subject=subject.strip(),
        email=normalized_email,
        display_name=display_name,
        email_verified=True,
        nonce=nonce,
    )
