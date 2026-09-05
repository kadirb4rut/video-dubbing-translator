#!/usr/bin/env python3
"""Run the real Lingowave AWS media path and save evidence.

This script intentionally uses the public application API and real media. It
does not stub providers, enqueue directly into SQS, or accept prerecorded
output. It can validate either the temporary CPU worker or the later GPU
worker; the selected worker is an infrastructure concern, not a test mock.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

TERMINAL_STATES = {"completed", "failed", "cancelled"}


class E2EError(RuntimeError):
    """Raised when the real API path returns an unexpected result."""


class Response:
    def __init__(self, *, status_code: int, body: bytes, method: str, url: str):
        self.status_code = status_code
        self.body = body
        self.method = method
        self.url = url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def request(self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> Response:
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            response = self.opener.open(request, timeout=180)
            return Response(status_code=response.status, body=response.read(), method=method, url=url)
        except urllib.error.HTTPError as error:
            return Response(status_code=error.code, body=error.read(), method=method, url=url)


def json_request(client: ApiClient, method: str, path: str, payload: dict[str, Any], *, extra_headers: dict[str, str] | None = None) -> Response:
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    return client.request(method, path, body=json.dumps(payload).encode("utf-8"), headers=headers)


def multipart_body(fields: dict[str, str], file_field: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = f"----lingowave-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def response_json(response: Response, *, expected: set[int] | None = None) -> Any:
    if expected is not None and response.status_code not in expected:
        raise E2EError(f"HTTP {response.status_code} from {response.method} {response.url}: {response.text[:500]}")
    try:
        return json.loads(response.body)
    except ValueError as exc:
        raise E2EError(f"Non-JSON response from {response.method} {response.url}") from exc


def authenticate(client: ApiClient, email: str, password: str) -> dict[str, Any]:
    response = json_request(client, "POST", "/api/auth/signup", {"email": email, "password": password, "display_name": "AWS Golden E2E"})
    if response.status_code == 409:
        response = json_request(client, "POST", "/api/auth/login", {"email": email, "password": password})
    return response_json(response, expected={200})


def download_to(client: ApiClient, endpoint: str, destination: Path) -> int:
    response = client.request("GET", endpoint)
    if response.status_code != 200:
        raise E2EError(f"Download failed with HTTP {response.status_code}: {response.text[:300]}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.body)
    return len(response.body)


def ffprobe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,format_name",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise E2EError(f"ffprobe rejected {path.name}: {result.stderr.strip()[:500]}")
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("LINGOWAVE_API_URL"), required=not bool(os.getenv("LINGOWAVE_API_URL")))
    parser.add_argument("--media", type=Path, required=True, help="Real spoken video to upload")
    parser.add_argument("--voice", type=Path, required=True, help="Real authorized reference voice audio")
    parser.add_argument("--email", required=True, help="Temporary or existing test account email")
    parser.add_argument("--password", required=True, help="Account password; never written to the evidence JSON")
    parser.add_argument("--target-language", default="es")
    parser.add_argument("--source-language")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/aws-golden-e2e"))
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    media = args.media.expanduser().resolve()
    voice = args.voice.expanduser().resolve()
    if not media.is_file() or not voice.is_file():
        raise E2EError("Both --media and --voice must point to existing files")
    if media.stat().st_size <= 0 or voice.stat().st_size <= 0:
        raise E2EError("Input media and reference voice must be non-empty")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    api_url = args.api_url.rstrip("/")
    started_at = time.time()

    client = ApiClient(api_url)
    authenticate(client, args.email, args.password)

    media_body, media_content_type = multipart_body({}, "upload", media.name, media.read_bytes(), "video/mp4")
    media_response = client.request("POST", "/api/media/upload", body=media_body, headers={"Content-Type": media_content_type})
    asset = response_json(media_response, expected={200})

    media_download = response_json(client.request("GET", f"/api/media/{asset['id']}/download"), expected={200})
    input_download_bytes = download_to(client, media_download["url"], output_dir / f"input-verified-{asset['id']}{media.suffix.lower()}")

    voice_body, voice_content_type = multipart_body(
        {
            "name": "AWS Golden E2E Reference",
            "declaration": "I own or am authorized to use this voice.",
            "authorized": "true",
        },
        "upload",
        voice.name,
        voice.read_bytes(),
        "audio/wav",
    )
    voice_response = client.request("POST", "/api/voices", body=voice_body, headers={"Content-Type": voice_content_type})
    voice_profile = response_json(voice_response, expected={200})

    idempotency_key = f"aws-golden-e2e-{uuid.uuid4()}"
    job_payload = {
        "media_asset_id": asset["id"],
        "operation": "dubbing",
        "source_language": args.source_language,
        "target_language": args.target_language,
        "preserve_voice": True,
        "keep_background": True,
        "lip_sync": False,
        "quality": "draft",
        "voice_profile_id": voice_profile["id"],
        "idempotency_key": idempotency_key,
    }
    job_response = json_request(client, "POST", "/api/jobs", job_payload, extra_headers={"Idempotency-Key": idempotency_key})
    job = response_json(job_response, expected={200})
    job_id = job["id"]

    deadline = time.monotonic() + args.timeout_seconds
    while True:
        detail_response = client.request("GET", f"/api/jobs/{job_id}")
        detail = response_json(detail_response, expected={200})
        if detail.get("state") in TERMINAL_STATES:
            break
        if time.monotonic() >= deadline:
            raise E2EError(f"Job {job_id} did not reach a terminal state before timeout")
        time.sleep(args.poll_seconds)

    evidence: dict[str, Any] = {
        "api_url": api_url,
        "job_id": job_id,
        "asset_id": asset["id"],
        "voice_profile_id": voice_profile["id"],
        "state": detail.get("state"),
        "input": {
            "path": str(media),
            "asset": asset,
            "download_verified": True,
            "download_bytes": input_download_bytes,
            "download_file": str(output_dir / f"input-verified-{asset['id']}{media.suffix.lower()}"),
        },
        "job": detail,
        "artifacts": [],
        "started_at_epoch": started_at,
        "finished_at_epoch": time.time(),
    }

    if detail.get("state") != "completed":
        evidence["success"] = False
        evidence_path = output_dir / f"job-{job_id}.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"job_id": job_id, "state": detail.get("state"), "evidence": str(evidence_path)}))
        return 1

    for artifact in detail.get("artifacts", []):
        filename = Path(artifact.get("filename") or f"{artifact['id']}.bin").name
        destination = output_dir / f"{job_id}-{artifact['name']}-{filename}"
        downloaded_bytes = download_to(client, f"/api/jobs/{job_id}/artifacts/{artifact['id']}/download", destination)
        evidence["artifacts"].append(
            {
                **artifact,
                "download_verified": True,
                "download_bytes": downloaded_bytes,
                "download_file": str(destination),
                "ffprobe": ffprobe(destination),
            }
        )

    evidence["success"] = True
    evidence_path = output_dir / f"job-{job_id}.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"job_id": job_id, "state": detail.get("state"), "evidence": str(evidence_path)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (E2EError, OSError, urllib.error.URLError) as exc:
        print(f"aws_golden_e2e: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
