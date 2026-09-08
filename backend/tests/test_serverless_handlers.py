from __future__ import annotations

from app.lambda_handler import handler as lambda_handler
from app.migration_handler import handler as migration_handler


def api_gateway_http_api_event(path: str = "/health") -> dict:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "api.example.com", "x-forwarded-proto": "https"},
        "requestContext": {
            "http": {"method": "GET", "path": path, "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1", "userAgent": "test"},
            "requestId": "serverless-test-request",
            "routeKey": "$default",
            "stage": "$default",
            "time": "01/Jan/2026:00:00:00 +0000",
            "timeEpoch": 1767225600000,
        },
        "isBase64Encoded": False,
    }


def test_lambda_handler_serves_api_gateway_http_api_event():
    response = lambda_handler(api_gateway_http_api_event(), {})

    assert response["statusCode"] == 200
    assert '"status":"ok"' in response["body"]


def test_migration_handler_runs_alembic_head(monkeypatch):
    calls: dict[str, str] = {}

    def fake_upgrade(config, revision):
        calls["script_location"] = config.get_main_option("script_location")
        calls["revision"] = revision

    monkeypatch.setattr("app.migration_handler.command.upgrade", fake_upgrade)

    assert migration_handler({}, {}) == {"status": "ok", "revision": "head"}
    assert calls["script_location"].endswith("/backend/alembic") or calls["script_location"].endswith("/app/alembic")
    assert calls["revision"] == "head"
