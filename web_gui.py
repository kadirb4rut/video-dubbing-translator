import cgi
import html
from http.cookies import CookieError, SimpleCookie
import json
import mimetypes
import os
import queue
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from Video_Translator import SOURCE_LANGUAGES, TARGET_LANGUAGES
from diarization.model_manager import status as diarization_status
from translation.model_manager import model_status as hymt2_model_status
from translation.registry import MODELS as HYMT2_MODELS
from live_dubbing.service_launcher import ensure_live_service, stop_managed_service
from compute_backends.providers import validate_provider
from compute_backends.settings import load_compute_settings, save_compute_settings
from compute_backends.onboarding import connection as cloud_connection
from engine.diagnostics import create_private_diagnostic_log, sanitize_for_display
from voxcpm_runtime import model_ready


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
MODEL_PATH = BASE_DIR / "vocal-remover" / "models" / "baseline.pth"
OUTPUT_DIR = BASE_DIR / "vocal-remover" / "final_video" / "final"
LATENTSYNC_DIR = BASE_DIR / "third_party" / "LatentSync"
LATENTSYNC_CHECKPOINTS = [
    LATENTSYNC_DIR / "scripts" / "inference.py",
    LATENTSYNC_DIR / "configs" / "unet" / "stage2_512.yaml",
    LATENTSYNC_DIR / "checkpoints" / "latentsync_unet.pt",
    LATENTSYNC_DIR / "checkpoints" / "whisper" / "tiny.pt",
]

state = {
    "process": None,
    "status": "Ready",
    "uploaded_video": "",
    "output_video": "",
    "logs": [],
}
log_queue = queue.Queue()
state_lock = threading.Lock()
live_browser_process = None
live_browser_lock = threading.Lock()
LIVE_BROWSER_STATUS = BASE_DIR / "temp" / "live-dubbing-browser-status.json"
LAST_OUTPUT = BASE_DIR / "temp" / "gui-last-output.json"
CLOUD_REQUEST_TOKEN = secrets.token_urlsafe(32)
LIVE_SERVICE_ORIGIN = "http://127.0.0.1:8766"
OUTPUT_ACCESS_COOKIE = "voxadub_output_access"
OUTPUT_ACCESS_TTL_SECONDS = 60 * 60
output_access_lock = threading.Lock()
output_access_grant = {"token": "", "path": "", "expires_at": 0.0}


def launch_live_service():
    """Start only the optional HTTP service; model initialization needs Start."""
    return ensure_live_service()


def launch_live_browser():
    """Start isolated Chrome; its child owns any service it starts."""
    global live_browser_process
    with live_browser_lock:
        if live_browser_process is not None and live_browser_process.poll() is None:
            return False, "The Live Dubbing Browser is already starting or open."
        from scripts.open_live_dubbing_browser import live_browser_profile_in_use
        if live_browser_profile_in_use():
            return False, "The Live Dubbing Browser is already open. Switch to its Google Chrome window."
        LIVE_BROWSER_STATUS.parent.mkdir(parents=True, exist_ok=True)
        LIVE_BROWSER_STATUS.write_text(
            json.dumps({"state": "starting", "message": "Preparing the separate Live Dubbing Browser."}),
            encoding="utf-8",
        )
        log_path = BASE_DIR / "temp" / "live-dubbing-browser-launch.log"
        log = log_path.open("a")
        try:
            environment = os.environ.copy()
            environment["LIVE_BROWSER_STATUS_PATH"] = str(LIVE_BROWSER_STATUS)
            preferences = load_compute_settings()
            environment["LIVE_COMPUTE_PROVIDER"] = preferences["live_provider"]
            environment["COLAB_SESSION_NAME"] = preferences["colab_session"]
            environment["COLAB_REMOTE_PROJECT"] = preferences["remote_project"]
            live_browser_process = subprocess.Popen(
                [sys.executable, str(BASE_DIR / "scripts" / "open_live_dubbing_browser.py")],
                cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"), env=environment,
            )
        except OSError as exc:
            log.close()
            LIVE_BROWSER_STATUS.write_text(
                json.dumps({"state": "error", "message": f"Could not start Live Dubbing Browser: {exc}"}),
                encoding="utf-8",
            )
            raise
        return True, "Starting Google Chrome with a separate Live Dubbing profile."


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def cancel_active_job():
    with state_lock:
        process = state["process"]
    if process is not None and process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


cloud_connection.cancel_job = cancel_active_job


def add_log(line):
    with state_lock:
        state["logs"].append(line)
        state["logs"] = state["logs"][-1200:]


def revoke_output_access():
    with output_access_lock:
        output_access_grant.update(token="", path="", expires_at=0.0)


def reset_gui_view_state():
    """Start a clean dashboard view without deleting any output files."""
    with state_lock:
        process = state.get("process")
        if process is not None and process.poll() is None:
            return False
        if state.get("status") not in ("Ready", "Done", "Failed"):
            return False
        state.update(process=None, status="Ready", uploaded_video="", output_video="", logs=[])
    revoke_output_access()
    while True:
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
    return True


def expected_output_path(uploaded_video, lip_sync=False, provider="local"):
    video_path = Path(uploaded_video)
    if provider in ("colab", "modal"):
        video_path = video_path.with_suffix(".mp4")
    if lip_sync:
        return OUTPUT_DIR / f"{video_path.stem}_lipsync{video_path.suffix or '.mp4'}"
    return OUTPUT_DIR / video_path.name


def latentsync_files_ready():
    return all(path.exists() for path in LATENTSYNC_CHECKPOINTS)


def run_process(command, status, output_path=None):
    with state_lock:
        if state["process"] is not None:
            return False, "Another process is already running."
        input_name = Path(state["uploaded_video"]).name if state["uploaded_video"] else ""
        state["status"] = status
        if output_path is not None:
            state["output_video"] = ""
        safe_stage = sanitize_for_display(status)
        safe_input = sanitize_for_display(input_name)
        first_line = f"{safe_stage} started"
        if safe_input:
            first_line += f" for {safe_input}"
        state["logs"] = [first_line + ".\n\n"]

    if output_path is not None:
        revoke_output_access()

    thread = threading.Thread(
        target=worker,
        args=(command, output_path, status, input_name),
        daemon=True,
    )
    thread.start()
    return True, "Started."


def worker(command, output_path=None, stage="Processing", input_name=""):
    diagnostic_path = None
    try:
        diagnostic_path = create_private_diagnostic_log(
            BASE_DIR / "temp" / "diagnostics",
            stage=stage,
            input_name=input_name,
        )
        with diagnostic_path.open("a", encoding="utf-8", errors="replace") as diagnostic:
            diagnostic.write("Command arguments are withheld from the browser log.\n\n")
            diagnostic.flush()
        process = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=(os.name != "nt"),
        )
        with state_lock:
            state["process"] = process

        with diagnostic_path.open("a", encoding="utf-8", errors="replace") as diagnostic:
            for line in process.stdout:
                diagnostic.write(line)
                diagnostic.flush()
                add_log(sanitize_for_display(line))

        return_code = process.wait()
        if return_code == 0:
            add_log("\nCompleted successfully.\n")
        else:
            safe_stage = sanitize_for_display(stage)
            safe_input = sanitize_for_display(Path(input_name).name) if input_name else "the request"
            add_log(
                f"\n{safe_stage} failed for {safe_input} (exit code {return_code}). "
                "A private diagnostic log was saved locally.\n"
            )
        with state_lock:
            state["status"] = "Done" if return_code == 0 else "Failed"
            if return_code == 0 and output_path and Path(output_path).exists():
                state["output_video"] = str(output_path)
                LAST_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
                temporary = LAST_OUTPUT.with_suffix(".tmp")
                temporary.write_text(json.dumps({"output_video": str(output_path)}))
                temporary.replace(LAST_OUTPUT)
    except Exception as exc:
        details = traceback.format_exc()
        diagnostic_saved = bool(diagnostic_path and diagnostic_path.is_file())
        if diagnostic_path is not None:
            try:
                with diagnostic_path.open("a", encoding="utf-8", errors="replace") as diagnostic:
                    diagnostic.write("\nWorker exception:\n")
                    diagnostic.write(details)
                diagnostic_saved = True
            except OSError:
                diagnostic_saved = False
        safe_stage = sanitize_for_display(stage)
        safe_input = sanitize_for_display(Path(input_name).name) if input_name else "the request"
        diagnostic_note = (
            "A private diagnostic log was saved locally."
            if diagnostic_saved
            else "The local diagnostic log could not be saved."
        )
        add_log(
            f"\nCould not complete {safe_stage} for {safe_input} ({type(exc).__name__}). "
            "Check the selected input and configured media dependencies, then retry. "
            f"{diagnostic_note}\n"
        )
        with state_lock:
            state["status"] = "Failed"
    finally:
        if "--compute" in command and command[command.index("--compute") + 1] in ("colab", "modal"):
            cloud_connection.complete()
        with state_lock:
            state["process"] = None


def json_response(handler, data, status=200, headers=None):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def open_output_folder(output_dir: Path, *, platform_name=None, opener_run=None, windows_opener=None):
    """Open the output folder when a desktop opener exists."""
    platform_name = sys.platform if platform_name is None else platform_name
    opener_run = subprocess.run if opener_run is None else opener_run
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        if platform_name == "darwin":
            result = opener_run(["open", str(output_dir)], check=False, capture_output=True, text=True)
        elif platform_name.startswith("win"):
            startfile = windows_opener or getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("Desktop folder opener is unavailable")
            startfile(str(output_dir))
            result = None
        else:
            result = opener_run(["xdg-open", str(output_dir)], check=False, capture_output=True, text=True)
        if result is not None and result.returncode != 0:
            raise OSError("Desktop folder opener returned an error")
        return {"ok": True}
    except (OSError, subprocess.SubprocessError):
        return {
            "ok": False,
            "code": "output_opener_unavailable",
            "message": "The output was saved, but this system has no working desktop folder opener. Open the output folder from your file manager.",
        }


def save_uploaded_file(file_obj, filename, directory: Path, *, expected_size=None):
    """Write bytes to a temporary sibling and atomically publish only a complete upload."""
    safe_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    safe_name = "".join(c for c in safe_name if ord(c) >= 32 and c not in '<>:"|?*').strip(" .")
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("Upload filename is empty or invalid.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=directory)
    temporary_path = Path(temporary_name)
    output_path = directory / safe_name
    written = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                written += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if expected_size is not None and written != expected_size:
            raise ValueError("Upload ended before the complete video was received.")
        if written == 0:
            raise ValueError("Uploaded video is empty.")
        os.replace(temporary_path, output_path)
        return output_path
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def page():
    compute_settings = load_compute_settings()
    colab_cli_disabled = os.name == "nt"
    selected_batch_provider = compute_settings["batch_provider"]
    selected_live_provider = compute_settings["live_provider"]
    if colab_cli_disabled:
        if selected_batch_provider == "colab":
            selected_batch_provider = "local"
        if selected_live_provider == "colab":
            selected_live_provider = "local"
    batch_provider_options = "\n".join(
        f'<option value="{value}" {"selected" if selected_batch_provider == value else ""} '
        f'{"disabled" if value == "colab" and colab_cli_disabled else ""}>{label}</option>'
        for value, label in (
            ("local", "Local — this computer"),
            ("colab", "Colab runtime (not available on Windows; full pipeline)" if colab_cli_disabled
             else "Colab CLI — G4 GPU"),
            ("modal", "Modal — full video dubbing"),
            ("modal-tts", "Modal speech only (legacy)"),
        )
        if value != "modal-tts" or selected_batch_provider == value
    )
    live_provider_options = "\n".join(
        f'<option value="{value}" {"selected" if selected_live_provider == value else ""} '
        f'{"disabled" if (value == "colab" and colab_cli_disabled) or value == "modal" else ""}>{label}</option>'
        for value, label in (
            ("local", "Local — this computer"),
            ("colab", "Colab runtime (not available on Windows; full live service)" if colab_cli_disabled
             else "Colab CLI — G4"),
            ("modal", "Modal — not available for Live"),
        )
    )
    colab_session_value = html.escape(compute_settings["colab_session"], quote=True)
    remote_project_value = html.escape(compute_settings["remote_project"], quote=True)
    source_options = "\n".join(
        f'<option value="{html.escape(code)}">{html.escape(name)}</option>'
        for name, code in SOURCE_LANGUAGES.items()
    )
    target_options = "\n".join(
        f'<option value="{html.escape(code)}" {"selected" if code == "en" else ""}>{html.escape(name)}</option>'
        for name, code in TARGET_LANGUAGES.items()
    )
    translation_model_options = "\n".join(
        f'<option value="{model.key}">{html.escape(model.display_name)}</option>'
        for model in HYMT2_MODELS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VoxaDub — Local-first AI video dubbing</title>
  <style>
    [hidden] {{ display:none !important; }}
    :root {{ color-scheme:dark; --bg:#15243a; --panel:#1d304a; --panel-raised:#243b5a; --line:#48617e; --text:#f4f8ff; --muted:#c3d2e5; --accent:#356cdb; --accent-hover:#4780ee; --green:#84e0aa; --red:#ffb4b2; }}
    * {{ box-sizing:border-box; }}
    html {{ overflow-x:hidden; }}
    body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1280px; margin:0 auto; padding:20px 24px 40px; }}
    header {{ padding:0 0 16px; margin-bottom:16px; border-bottom:1px solid #27354a; }}
    h1 {{ font-size:22px; line-height:1.2; letter-spacing:-.035em; margin:0 0 5px; }}
    h2 {{ font-size:16px; line-height:1.25; letter-spacing:-.018em; margin:0; }}
    .subtitle {{ color:var(--muted); font-size:13px; }}
    .layout {{ display:grid; grid-template-columns:minmax(0, .92fr) minmax(0, 1.08fr); gap:16px; align-items:start; }}
    .workflow,.panel,.step,.row > div {{ min-width:0; }}
    .panel {{ background:var(--panel); border:1px solid #29384d; border-radius:10px; padding:16px; box-shadow:0 12px 36px rgba(0,0,0,.12); }}
    .live-panel {{ margin-bottom:16px; }}
    .live-panel > .row {{ grid-template-columns:1fr; }}
    .pairing-box {{ margin-top:12px; padding:12px; border:1px solid #3a5270; border-radius:8px; background:#182a40; }}
    .pairing-box h3 {{ margin:0; color:#e3eaff; font-size:13px; }}
    .pairing-box .actions {{ margin-top:8px; }}
    #livePairingCode {{ display:block; min-height:0; margin-top:8px; color:#eaf0ff; font:12px/1.5 Menlo,Consolas,monospace; overflow-wrap:anywhere; user-select:all; }}
    #livePairingMessage[data-state="error"] {{ color:#ffb7b7; }}
    #livePairingMessage[data-state="success"] {{ color:#a8f0c6; }}
    .panel-heading {{ display:flex; align-items:flex-start; gap:10px; margin-bottom:12px; }}
    .panel-icon {{ flex:0 0 32px; height:32px; border-radius:8px; display:grid; place-items:center; color:#abbaff; background:#283965; font-size:16px; font-weight:700; }}
    .panel-description {{ color:var(--muted); margin:3px 0 0; font-size:13px; }}
    .step {{ margin:0 0 16px; }}
    .step + .step {{ padding-top:16px; border-top:1px solid #29384d; }}
    .step h2 {{ font-size:14px; font-weight:700; margin:0 0 8px; }}
    label {{ display:block; margin:0 0 5px; color:#d9e2f5; font-weight:650; font-size:12px; }}
    input[type=file],input[type=number],input[type=text],input[type=password],select,textarea {{ width:100%; max-width:100%; min-width:0; min-height:36px; border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; color:var(--text); background:#182a40; }}
    input[type=file] {{ padding:4px; font-size:13px; }}
    input::placeholder,textarea::placeholder {{ color:#98a8c1; opacity:1; }}
    input[type=file]::file-selector-button {{ border:1px solid #40516d; border-radius:6px; padding:4px 8px; margin-right:8px; background:#26334a; color:var(--text); font:inherit; cursor:pointer; }}
    textarea {{ resize:vertical; min-height:64px; }}
    select {{ cursor:pointer; }}
    button {{ width:100%; min-height:36px; padding:7px 12px; border:1px solid #394b65; border-radius:8px; color:#eaf0ff; background:#243148; font:inherit; font-weight:650; font-size:13px; line-height:1.3; cursor:pointer; transition:background .15s,border-color .15s; }}
    button:hover:not(:disabled) {{ background:#30415d; border-color:#59708f; }}
    button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
    button.primary:hover:not(:disabled) {{ background:var(--accent-hover); }}
    button.danger {{ background:#3c2734; border-color:#71404c; color:#ffd4d2; }}
    button:disabled {{ opacity:.5; cursor:not-allowed; }}
    :focus-visible {{ outline:3px solid #a9b8ff; outline-offset:3px; }}
    .row,.actions {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
    .hint {{ color:var(--muted); font-size:12px; line-height:1.45; margin:6px 0 0; overflow-wrap:anywhere; }}
    .output-hint {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .statusbar {{ display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px; }}
    .statusbar h2 {{ margin-right:auto !important; }}
    .badge {{ border-radius:999px; padding:3px 8px; background:#293a61; color:#cbd6ff; font-size:12px; font-weight:700; }}
    .badge.ok {{ background:#173f35; color:#a8f0c6; }}
    .badge.bad {{ background:#542d36; color:#ffd0cf; }}
    .console {{ height:220px; overflow:auto; background:#142338; color:#cbd7ea; border:1px solid #29384d; border-radius:9px; padding:12px; font:12px/1.5 Menlo,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .checkline {{ display:flex; align-items:flex-start; gap:10px; border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:#182a40; color:#dce5f5; font-size:13px; font-weight:550; }}
    .checkline input {{ width:16px; height:16px; margin:1px 0 0; flex:0 0 auto; accent-color:var(--accent); }}
    details {{ border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:#182a40; }}
    summary {{ cursor:pointer; color:#e3eaff; font-weight:650; font-size:13px; }}
    details[open] > summary {{ margin-bottom:10px; }}
    .advanced {{ margin:14px 0; }}
    .advanced .step:last-child {{ margin-bottom:0; }}
    a {{ color:#aebeff; text-underline-offset:3px; }}
    .preview {{ display:none; margin-bottom:16px; }}
    .preview.active {{ display:block; }}
    .preview video {{ display:none; width:100%; max-height:320px; aspect-ratio:16/9; border-radius:9px; background:#070c14; }}
    .preview.active video {{ display:block; }}
    .preview-empty {{ width:100%; height:clamp(180px, 20vw, 260px); display:grid; place-content:center; text-align:center; color:var(--muted); border:1px dashed #40516b; border-radius:9px; background:#182a40; margin-bottom:16px; padding:16px; }}
    .preview.active + .preview-empty {{ display:none; }}
    .preview-empty strong {{ color:#dfe7f7; font-size:14px; }}
    .preview-empty span {{ margin-top:5px; font-size:13px; }}
    @media(max-width:950px) {{ main {{ padding:20px; }} .layout {{ grid-template-columns:1fr; }} }}
    @media(max-width:600px) {{ main {{ padding:16px 12px 32px; }} h1 {{ font-size:20px; }} .panel {{ padding:14px; }} .row,.actions {{ grid-template-columns:1fr; }} .console {{ height:200px; }} input:not([type=checkbox]):not([type=radio]),select,button {{ min-height:40px; }} }}
    @media(pointer:coarse) {{ input[type=file],input[type=number],input[type=text],input[type=password],select,button,.checkline,summary {{ min-height:44px; }} }}
    .activity {{ position:sticky; top:12px; z-index:10; display:flex; align-items:center; gap:12px; box-shadow:0 12px 30px rgba(0,0,0,.25); margin:0 0 16px; padding:10px 12px; border:1px solid #547da9; border-radius:10px; background:#203c5b; color:#f2f8ff; font-size:13px; }}
    .activity[hidden] {{ display:none; }}
    #colabUsage {{ position:relative; top:auto; }}
    .activity[data-state="error"] {{ border-color:#c1777e; background:#49303e; }}
    .activity[data-state="success"] {{ border-color:#498d80; background:#1e493f; }}
    .activity-spinner {{ width:17px; height:17px; flex:0 0 17px; border:2px solid #9fc5ed; border-top-color:#fff; border-radius:50%; animation:spin .8s linear infinite; }}
    .activity:not([data-state="busy"]) .activity-spinner {{ display:none; }}
    button.is-busy::before {{ content:""; display:inline-block; width:12px; height:12px; border:2px solid currentColor; border-top-color:transparent; border-radius:50%; margin-right:8px; vertical-align:-2px; animation:spin .8s linear infinite; }}
    @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
    @media(prefers-reduced-motion:reduce) {{ button {{ transition:none; }} .activity-spinner,button.is-busy::before {{ animation:none; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>VoxaDub</h1>
    <div class="subtitle">Dub a live stream or translate a video, all in one place.</div>
  </header>
  <div id="activity" class="activity" data-state="idle" role="status" aria-live="polite" hidden><span class="activity-spinner" aria-hidden="true"></span><span id="activityText"></span></div>
  <div id="colabUsage" class="activity" data-state="info" role="status" aria-live="polite" hidden><span id="colabUsageText"></span> <a href="https://colab.research.google.com/signup" target="_blank" rel="noopener noreferrer">Manage Colab compute units</a></div>
  <section class="layout">
  <div class="workflow">
  <section class="panel live-panel">
    <div class="panel-heading"><span class="panel-icon" aria-hidden="true">◉</span><div><h2>Live Dubbing</h2><p class="panel-description">Watch and dub a supported video in your browser.</p></div></div>
    <div class="row">
      <div>
        <label for="liveProvider">Live Dubbing · GPU provider</label>
        <select id="liveProvider" onchange="updateComputeHint()">{live_provider_options}</select>
      </div>
      <div class="actions" style="align-items:end">
        <button id="connectLiveButton" class="secondary" onclick="runAction(this, 'connectLiveGpu', () =&gt; connectLiveGpu())">Connect &amp; prepare Colab</button>
        <button class="primary" onclick="runAction(this, 'openLiveBrowser', () =&gt; openLiveBrowser())">Open Live Dubbing Browser</button>
      </div>
    </div>
    <p id="liveProviderHint" class="hint" role="status" aria-live="polite">Local inference runs on this computer. Colab Live keeps its GPU connected until you choose Disconnect GPU.</p>
    <p class="hint">Chrome uses a separate app profile. Capture starts only after your confirmation in the browser.</p>
    <section class="pairing-box" aria-labelledby="pairingHeading">
      <h3 id="pairingHeading">Connect the Live Dubbing extension</h3>
      <p class="hint">After opening the Live Dubbing Browser, create a one-use code here and enter it in the extension panel. The code expires after 120 seconds.</p>
      <div class="actions">
        <button id="createPairingButton" class="secondary" onclick="runAction(this, 'createPairingCode', () =&gt; createLivePairingCode())">Create one-use pairing code</button>
        <button id="copyPairingButton" class="secondary" onclick="copyLivePairingCode()" disabled>Copy pairing code</button>
      </div>
      <output id="livePairingCode" aria-label="One-use pairing code"></output>
      <p id="livePairingMessage" class="hint" role="status" aria-live="polite">Open the Live Dubbing Browser first, then create a fresh code for that session.</p>
    </section>
  </section>
    <aside class="panel">
      <div class="panel-heading"><span class="panel-icon" aria-hidden="true">▶</span><div><h2>Offline Video Dubbing</h2><p class="panel-description">Translate a video file into another language.</p></div></div>
      <div class="step">
        <h2>Compute location</h2>
        <label for="batchProvider">Offline dubbing · GPU provider</label>
        <select id="batchProvider" onchange="updateComputeHint()">{batch_provider_options}</select>
        <div id="cloudConnect" hidden>
          <p id="cloudStatus" class="hint" role="status" aria-live="polite">Connect your account and prepare the GPU before starting.</p>
          <div class="actions" style="margin-top:10px">
            <button id="cloudConnectButton" class="secondary" onclick="runAction(this, 'connectCloud', () =&gt; connectCloud())">Connect &amp; prepare GPU</button>
            <button id="cloudDisconnectButton" class="secondary" onclick="runAction(this, 'disconnectCloud', () =&gt; disconnectCloud())">Disconnect GPU</button>
          </div>
          <div id="cloudAuth" hidden style="margin-top:10px">
            <a id="cloudAuthLink" target="_blank" rel="noopener noreferrer" hidden>Open account authorization</a>
            <label for="cloudAuthCode" class="hint">If Google displays an authorization code, paste it here. Otherwise leave this empty after granting access.</label>
            <input id="cloudAuthCode" type="password" autocomplete="off" placeholder="Authorization code, if requested">
            <button class="secondary" onclick="runAction(this, 'confirmCloudAuth', () =&gt; confirmCloudAuth())">Continue after authorization</button>
          </div>
          <details style="margin-top:10px"><summary>Optional speaker model access</summary>
            <p class="hint">For Community-1 speaker tracking, accept its Hugging Face terms and enter a read token. The token is used only for this setup and is never saved. Without it, existing per-segment speaker references are used.</p>
            <a href="https://huggingface.co/pyannote/speaker-diarization-community-1" target="_blank" rel="noopener noreferrer">Community-1 access terms</a>
            <input id="cloudHfToken" type="password" autocomplete="off" placeholder="Optional Hugging Face read token">
          </details>
          <details style="margin-top:10px"><summary>Connection log</summary><pre id="cloudLog" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto"></pre></details>
        </div>
        <details style="margin-top:12px"><summary>Live Dubbing and advanced connection settings</summary>
        <label for="colabSession" style="margin-top:8px">Colab CLI session</label>
        <input id="colabSession" type="text" value="{colab_session_value}" placeholder="Your active session name">
        <label for="remoteProject" style="margin-top:8px">Colab project folder</label>
        <input id="remoteProject" type="text" value="{remote_project_value}">
        <button class="secondary" style="margin-top:8px" onclick="runAction(this, 'saveComputeSettings', () =&gt; saveComputeSettings())">Save compute choices</button>
        </details>
        <div id="computeHint" class="hint">Choose where each task uses its compute. Cloud sessions stay open until Disconnect GPU. Allocated resources may continue consuming credits.</div>
      </div>
      <div class="step">
        <h2>Video</h2>
        <label for="video">Choose a video file</label>
        <input id="video" type="file" accept="video/*">
        <button class="secondary" style="margin-top:10px" onclick="runAction(this, 'uploadVideo', () =&gt; uploadVideo())">Upload Selected Video</button>
        <div id="videoHint" class="hint">No video uploaded yet.</div>
      </div>
      <div class="step">
        <h2>Languages</h2>
        <div class="row">
          <div>
            <label for="source">Source</label>
            <select id="source">{source_options}</select>
          </div>
          <div>
            <label for="target">Target</label>
            <select id="target">{target_options}</select>
          </div>
        </div>
      </div>
      <div class="step">
        <h2>Generate</h2>
        <div class="actions">
          <button id="startButton" class="primary" onclick="runAction(this, 'startDubbing', () =&gt; startDubbing())">Start Dubbing</button>
          <button class="danger" onclick="runAction(this, 'cancelRun', () =&gt; cancelRun())">Cancel</button>
        </div>
      </div>
      <details class="advanced"><summary>Advanced model, speaker and lip-sync settings</summary>
      <div class="step">
        <h2>Models</h2>
        <div class="hint">VoxCPM2 uses an Apache-2.0 checkpoint and produces 48 kHz cloned speech. The pinned download is approximately 5 GB.</div>
        <button class="secondary" style="margin-top:10px" data-local-setup onclick="runAction(this, 'downloadModel', () =&gt; downloadModel())">Download / Check Required Models</button>
        <div id="modelHint" class="hint">Checking models...</div>
        <label for="translationModel" style="margin-top:12px">Translation model</label>
        <select id="translationModel">{translation_model_options}</select>
        <div id="translationModelHint" class="hint">Checking local Hy-MT2 models...</div>
        <div class="actions" style="margin-top:8px">
          <button class="secondary" data-local-setup onclick="runAction(this, 'setupHyMT2', () =&gt; setupHyMT2())">Setup Runtime</button>
          <button class="secondary" data-local-setup onclick="runAction(this, 'downloadHyMT2', () =&gt; downloadHyMT2())">Download Selected</button>
        </div>
        <label for="translationDevice" style="margin-top:10px">Translation device</label>
        <select id="translationDevice">
          <option value="auto">Auto</option><option value="cpu">CPU</option>
          <option value="mps">Apple MPS</option><option value="cuda">CUDA</option>
        </select>
        <button class="secondary" style="margin-top:12px" data-local-setup onclick="runAction(this, 'setupDiarization', () =&gt; setupDiarization())">Setup / Download Community-1</button>
        <div id="diarizationHint" class="hint">Checking diarization...</div>
        <button class="secondary" style="margin-top:10px" data-local-setup onclick="runAction(this, 'setupLatentSync', () =&gt; setupLatentSync())">Setup / Check LatentSync</button>
        <div id="latentSyncHint" class="hint">Checking LatentSync...</div>
      </div>
      <div class="step">
        <h2>Speakers and terminology</h2>
        <label class="checkline" for="diarization">
          <input id="diarization" type="checkbox" checked>
          Keep voices consistent by speaker
        </label>
        <div class="row" style="margin-top:8px">
          <div><label for="numSpeakers">Exact count</label><input id="numSpeakers" type="number" min="1" placeholder="Auto"></div>
          <div><label for="speakerBounds">Bounds (min,max)</label><input id="speakerBounds" type="text" placeholder="e.g. 2,5"></div>
        </div>
        <label for="glossary" style="margin-top:10px">Optional glossary JSON</label>
        <textarea id="glossary" placeholder='{{"OpenBMB":"OpenBMB","CUDA":"CUDA"}}'></textarea>
      </div>
      <div class="step">
        <h2>Lip-sync</h2>
        <label class="checkline" for="lipSync">
          <input id="lipSync" type="checkbox">
          Apply LatentSync after dubbing
        </label>
        <div class="hint">Requires a separate LatentSync setup and an NVIDIA CUDA GPU.</div>
      </div>
      </details>
      <button class="secondary" onclick="runAction(this, 'openOutput', () =&gt; openOutput())">Open Output Folder</button>
      <div class="hint output-hint" title="{html.escape(str(OUTPUT_DIR))}">Output: {html.escape(str(OUTPUT_DIR))}</div>
    </aside>
  </div>
    <section class="panel results-panel" aria-label="Output and process status">
      <div id="preview" class="preview">
        <div class="statusbar">
          <h2 style="margin:0;font-size:16px">Final Video</h2>
          <a id="videoLink" class="hint" href="#">Authorize and preview</a>
          <a id="downloadLink" class="hint" href="#">Authorize and download</a>
        </div>
        <video id="finalVideo" controls playsinline preload="metadata"></video>
      </div>
      <div class="preview-empty"><strong>Your finished video will appear here</strong><span>Choose a video and start dubbing to see the result.</span></div>
      <div class="statusbar">
        <h2 style="margin:0;font-size:16px">Process Log</h2>
        <span id="status" class="badge">Ready</span>
      </div>
      <div id="console" class="console">Ready. Upload a video, download the model once, then start dubbing.</div>
    </section>
  </section>
</main>
<script>
let uploadedVideoPath = '';
let uploadedFile = null;
let currentVideoUrl = '';
let currentActivity = '';
let activitySeenRunning = false;
let activityErrorVersion = 0;
function activity(message, state = 'busy') {{
  const box = document.getElementById('activity');
  box.hidden = false; box.dataset.state = state; box.setAttribute('role', state === 'error' ? 'alert' : 'status');
  document.getElementById('activityText').textContent = message;
}}
async function runAction(button, name, action) {{
  if (button.dataset.busy === 'true') return;
  button.dataset.busy = 'true'; button.classList.add('is-busy'); button.setAttribute('aria-busy', 'true');
  const errors = activityErrorVersion;
  const cloudActions = ['connectCloud','connectLiveGpu','disconnectCloud','confirmCloudAuth'];
  const jobActions = ['startDubbing','cancelRun','uploadVideo','downloadModel','setupHyMT2','downloadHyMT2','setupDiarization','setupLatentSync'];
  currentActivity = cloudActions.includes(name) ? 'cloud' : jobActions.includes(name) ? 'job' : name === 'openLiveBrowser' ? 'launch' : 'other';
  activitySeenRunning = false;
  const messages = {{connectCloud:'Connecting to your selected GPU…',connectLiveGpu:'Preparing Colab for Live Dubbing…',disconnectCloud:'Requesting GPU disconnection…',confirmCloudAuth:'Checking authorization…',openLiveBrowser:'Preparing the Live Dubbing Browser…',uploadVideo:'Uploading the selected video…',startDubbing:'Preparing your dubbing request…',cancelRun:'Requesting cancellation…',downloadModel:'Checking required models…',setupHyMT2:'Preparing the translation runtime…',downloadHyMT2:'Starting model download…',setupDiarization:'Preparing the speaker model…',setupLatentSync:'Preparing lip-sync…',saveComputeSettings:'Saving compute choices…',openOutput:'Opening the output folder…'}};
  activity(messages[name] || 'Working…');
  try {{
    await action();
    if (activityErrorVersion === errors) {{
      if (name === 'saveComputeSettings') activity('Compute choices saved.', 'success');
      else if (name === 'openOutput') activity('Output folder requested.', 'success');
      else if (name === 'uploadVideo') activity('Video uploaded and ready.', 'success');
      else if (name === 'createPairingCode') activity('One-use pairing code created. Enter it in the Live Dubbing extension.', 'success');
      else if (currentActivity === 'other') activity('Request completed.', 'success');
      else if (!cloudActions.includes(name) && name !== 'openLiveBrowser') activity('Request submitted. Follow the status and log for updates.', 'info');
    }}
  }} catch (error) {{ if (activityErrorVersion === errors) showError(error); }}
  finally {{ delete button.dataset.busy; button.classList.remove('is-busy'); button.removeAttribute('aria-busy'); }}
}}

function updateComputeHint() {{
  const batch = document.getElementById('batchProvider').value;
  const live = document.getElementById('liveProvider').value;
  const hint = document.getElementById('computeHint');
  const messages = [];
  const remote = ['colab', 'modal'].includes(batch);
  const liveRemote = live === 'colab';
  const activeCloud = cloudState.provider && cloudState.provider !== 'local' && cloudState.stage !== 'disconnected';
  document.getElementById('cloudConnect').hidden = !remote && !liveRemote && !activeCloud;
  document.getElementById('cloudConnectButton').hidden = !remote;
  document.getElementById('connectLiveButton').hidden = !liveRemote;
  document.getElementById('connectLiveButton').disabled = ['connecting','preparing','authorizing','disconnecting'].includes(cloudState.stage);
  document.getElementById('connectLiveButton').textContent = cloudState.provider === 'colab' && cloudState.stage === 'ready' && cloudState.live
    ? 'Colab ready' : cloudState.stage === 'cache_failed' && cloudState.provider === 'colab' ? 'Retry Colab setup' : 'Connect & prepare Colab';
  const liveHint = document.getElementById('liveProviderHint');
  liveHint.textContent = live === 'colab'
    ? (cloudState.provider === 'colab' && cloudState.stage === 'ready' && cloudState.live
      ? 'Colab G4 is ready. Open the browser, choose a permitted source, and start capture. The GPU stays connected until Disconnect GPU.'
      : 'The browser and video stay on this computer; model processing runs on Colab G4. Connect here or choose Open Live Dubbing Browser to connect and launch in one flow.')
    : 'Live processing runs on this computer. Modal Live Dubbing is not available yet.';
  if (!remote && activeCloud) messages.push('A cloud GPU is still connected. Use Disconnect GPU to release it; selecting Local does not close that connection.');
  document.querySelectorAll('[data-local-setup]').forEach(button => button.hidden = remote);
  document.getElementById('translationDevice').disabled = remote;
  if (remote) document.getElementById('translationDevice').value = 'auto';
  if (batch === 'modal') messages.push('The complete offline pipeline runs on Modal GPU. First connection prepares a persistent model cache. The Modal app and GPU stay connected until you press Disconnect GPU; model storage remains afterwards.');
  if (batch === 'modal-tts') {{
    messages.push('Modal runs VoxCPM2 speech only. After the job, the app requests Modal’s shortest idle window for GPU containers; actual shutdown can take longer. The deployed app and persistent model Volume stay ready for later use.');
  }}
  if (batch === 'colab' || live === 'colab') {{
    messages.push('Connect & prepare GPU handles Colab CLI login, the dedicated G4 session, application setup and missing model downloads. Models are stored on the Colab VM and must be downloaded again after the VM is terminated. Colab stays connected until you press Disconnect GPU.');
  }}
  if (messages.length) {{
    hint.textContent = messages.join(' ');
  }} else {{
    hint.textContent = 'All selected processing runs on this computer using its available CPU/GPU.';
  }}
}}

async function cloudApi(path, payload) {{
  return api(path, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json', 'X-Compute-Token': '{CLOUD_REQUEST_TOKEN}' }}, body: JSON.stringify(payload || {{}}) }});
}}

async function connectCloud(mode = 'offline') {{
  currentActivity = 'cloud'; activitySeenRunning = false;
  try {{
    await saveComputeSettings();
    const tokenInput = document.getElementById('cloudHfToken');
    const hfToken = tokenInput.value;
    tokenInput.value = '';
    await cloudApi('/api/cloud-connect', {{provider: document.getElementById(mode === 'live' ? 'liveProvider' : 'batchProvider').value,
      model: document.getElementById('translationModel').value,
      diarization: document.getElementById('diarization').checked,
      lipSync: document.getElementById('lipSync').checked, live: mode === 'live', hfToken}});
    document.getElementById('cloudConnect').hidden = false;
    await refreshCloud();
  }} catch(error) {{ showError(error); }}
}}

async function connectLiveGpu() {{
  if (document.getElementById('liveProvider').value !== 'colab') return;
  pendingLive = false;
  await connectCloud('live');
}}

async function confirmCloudAuth() {{
  try {{
    const input = document.getElementById('cloudAuthCode');
    const value = input.value; input.value = '';
    await cloudApi('/api/cloud-input', {{value}});
    await refreshCloud();
  }} catch(error) {{ showError(error); }}
}}

async function disconnectCloud() {{
  try {{ await cloudApi('/api/cloud-disconnect'); await refreshCloud(); }}
  catch(error) {{ showError(error); }}
}}

let cloudState = {{stage:'disconnected'}};
let cloudWasBusy = false;
let pendingDubbing = false;
let pendingLive = false;
async function refreshCloud() {{
  const result = await cloudApi('/api/cloud-status');
  cloudState = result;
  const usage = document.getElementById('colabUsage');
  const usageText = document.getElementById('colabUsageText');
  const cuState = result.cu_status;
  usage.hidden = result.provider !== 'colab' || !cuState;
  usage.dataset.state = ['empty','low'].includes(cuState) ? 'error' : 'info';
  if (cuState === 'checking') usageText.textContent = 'Checking Colab Compute Unit balance…';
  else if (cuState === 'unknown') usageText.textContent = 'Could not check your Colab balance. Connection will continue; check your account if it fails.';
  else if (cuState === 'empty') usageText.textContent = 'Colab balance: 0 CU. Paid compute units are exhausted; GPU access depends on your account and availability.';
  else if (cuState === 'low') usageText.textContent = `Colab balance: ${{result.cu_balance}} CU — low balance. G4 access and remaining runtime may be limited.`;
  else if (cuState === 'available') usageText.textContent = `Colab balance: ${{result.cu_balance}} CU. GPU availability is not guaranteed.`;
  if (['connecting','preparing','authorizing','disconnecting'].includes(result.stage)) {{
    // A cloud setup can outlive the button action or a page reload. Always
    // show its current stage, even when this tab did not start the operation.
    cloudWasBusy = true;
    activitySeenRunning = true;
    activity(result.message);
  }} else if (['error','cache_failed'].includes(result.stage) && (currentActivity === '' || currentActivity === 'cloud' || cloudWasBusy)) {{
    activity(result.message, 'error'); currentActivity = ''; cloudWasBusy = false;
  }} else if (currentActivity === 'cloud' || cloudWasBusy) {{
    if (result.stage === 'ready') {{ activity(result.message, 'success'); currentActivity = ''; cloudWasBusy = false; }}
    else if (cloudWasBusy && result.stage === 'disconnected') {{ activity(result.message, 'success'); currentActivity = ''; cloudWasBusy = false; }}
  }}
  document.getElementById('cloudStatus').textContent = result.message;
  document.getElementById('cloudLog').textContent = result.logs.join('\\n');
  document.getElementById('cloudAuth').hidden = !result.needsInput && !result.authUrl;
  const link = document.getElementById('cloudAuthLink');
  link.hidden = !result.authUrl;
  if (result.authUrl) link.href = result.authUrl;
  else link.removeAttribute('href');
  document.getElementById('cloudConnectButton').disabled = ['connecting','preparing','authorizing','disconnecting'].includes(result.stage);
  document.getElementById('cloudConnectButton').textContent = result.stage === 'cache_failed' ? 'Retry connection' : 'Connect & prepare GPU';
  document.getElementById('cloudDisconnectButton').disabled = result.stage === 'disconnected' || result.stage === 'disconnecting';
  if (result.session) document.getElementById('colabSession').value = result.session;
  if (result.project) document.getElementById('remoteProject').value = result.project;
  updateComputeHint();
  if (['error','cache_failed','disconnected'].includes(result.stage)) {{ pendingDubbing = false; pendingLive = false; }}
  if (result.stage === 'ready' && pendingDubbing) {{ pendingDubbing = false; currentActivity = 'job'; activitySeenRunning = false; activity('Preparing your dubbing request…'); await startDubbing(); }}
  if (result.stage === 'ready' && pendingLive) {{ pendingLive = false; currentActivity = 'launch'; activity('Preparing the Live Dubbing Browser…'); await openLiveBrowser(true); }}
}}

async function saveComputeSettings() {{
  const payload = {{
    batch_provider: document.getElementById('batchProvider').value,
    live_provider: document.getElementById('liveProvider').value,
    colab_session: document.getElementById('colabSession').value.trim(),
    remote_project: document.getElementById('remoteProject').value.trim(),
  }};
  try {{
    await api('/api/compute-settings', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(payload) }});
    return payload;
  }} catch (error) {{ showError(error); throw error; }}
}}

async function openLiveBrowser(colabReadyChecked = false) {{
  if (document.getElementById('liveProvider').value === 'modal') {{
    showError(new Error('Modal Live Dubbing is not implemented yet. Choose Local or Colab CLI.'));
    return;
  }}
  const currentBrowser = await fetch('/api/live-browser-status').then(response => response.json());
  if (currentBrowser.profileInUse) {{
    activity('Live Dubbing Chrome is already open with its extension. Switch to that Google Chrome window.', 'success');
    currentActivity = '';
    return;
  }}
  if (document.getElementById('liveProvider').value === 'colab' && !colabReadyChecked) {{
    pendingLive = true;
    if (cloudState.provider === 'colab' && ['connecting','preparing','authorizing'].includes(cloudState.stage)) return;
    // Revalidate even an in-memory "ready" state. Colab can be stopped outside
    // this GUI, and a stale ready flag otherwise leaves the browser launch page
    // waiting on a tunnel to a session that no longer exists.
    await connectCloud('live');
    return;
  }}
  try {{
    await saveComputeSettings();
    await api('/api/open-live-browser', {{ method: 'POST' }});
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {{
      const response = await fetch('/api/live-browser-status');
      const status = await response.json();
      if (status.state === 'ready') {{
        activity('Live Dubbing Chrome is open on YouTube. Choose a supported video, then open Local Adaptive Live Dubbing from Chrome’s Extensions menu. Capture starts only after your confirmation.', 'success');
        return;
      }}
      if (status.state === 'error') throw new Error(status.message || 'Live Dubbing Chrome could not start.');
      if (status.state === 'closed') throw new Error(status.message || 'Live Dubbing Chrome closed before it was ready.');
      activity(status.message || 'Starting the separate Live Dubbing Chrome window…', 'busy');
      await new Promise(resolve => setTimeout(resolve, 900));
    }}
    activity('Chrome is still starting. The dashboard remains open; check the separate Chrome window or the Live Dubbing launch log.', 'info');
  }} catch (error) {{ throw error; }}
}}

let livePairingCode = '';
let livePairingExpiryTimer = null;
async function createLivePairingCode() {{
  const output = document.getElementById('livePairingCode');
  const message = document.getElementById('livePairingMessage');
  const copyButton = document.getElementById('copyPairingButton');
  livePairingCode = '';
  output.textContent = '';
  copyButton.disabled = true;
  message.dataset.state = 'info';
  message.textContent = 'Checking the Live Dubbing connection…';
  if (livePairingExpiryTimer) clearInterval(livePairingExpiryTimer);
  try {{
    const serviceOrigin = '{LIVE_SERVICE_ORIGIN}';
    const sessionResponse = await fetch(serviceOrigin + '/api/pairing-session', {{
      credentials: 'include', cache: 'no-store'
    }});
    if (!sessionResponse.ok) {{
      const detail = await sessionResponse.text();
      if ([403, 404].includes(sessionResponse.status)) {{
        throw new Error('The Live Dubbing service needs refreshing. Close its browser window, reopen it from this panel, then create a new code.');
      }}
      throw new Error(detail || 'Local pairing authorization failed.');
    }}
    const response = await fetch(serviceOrigin + '/api/pair', {{
      method: 'POST', credentials: 'include', cache: 'no-store',
      headers: {{'Content-Type': 'application/json'}}, body: '{{}}'
    }});
    const result = await response.json().catch(() => ({{}}));
    if (!response.ok) {{
      if (response.status === 403) {{
        throw new Error('The local pairing session was not authorized. Reload the main panel and try again.');
      }}
      throw new Error(result.error || 'Could not create a pairing code.');
    }}
    livePairingCode = result.code;
    output.textContent = livePairingCode;
    copyButton.disabled = false;
    const expiresAt = Date.now() + (Number(result.expires_seconds) || 120) * 1000;
    const updateExpiry = () => {{
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      if (!remaining) {{
        clearInterval(livePairingExpiryTimer);
        livePairingExpiryTimer = null;
        livePairingCode = '';
        output.textContent = '';
        copyButton.disabled = true;
        message.dataset.state = 'info';
        message.textContent = 'Pairing code expired. Create a fresh code for this session.';
      }} else {{
        message.dataset.state = 'success';
        message.textContent = `Code ready. Enter it in the extension panel within ${{remaining}} seconds.`;
      }}
    }};
    updateExpiry();
    livePairingExpiryTimer = setInterval(updateExpiry, 1000);
  }} catch (error) {{
    message.dataset.state = 'error';
    if (error instanceof TypeError) {{
      message.textContent = 'Live Dubbing service is not reachable. Open Live Dubbing Browser and keep that session open, then try again.';
      throw new Error(message.textContent);
    }}
    message.textContent = error.message || 'Could not create a pairing code.';
    throw error;
  }}
}}

async function copyLivePairingCode() {{
  if (!livePairingCode) return;
  const message = document.getElementById('livePairingMessage');
  try {{
    await navigator.clipboard.writeText(livePairingCode);
    message.dataset.state = 'success';
    message.textContent = 'Pairing code copied. Paste it into “Pairing code from the local application” in the extension panel.';
  }} catch (error) {{
    message.dataset.state = 'error';
    message.textContent = 'Copy was blocked by the browser. Select the visible code and copy it manually.';
  }}
}}

async function api(path, options = {{}}) {{
  if (options.method === 'POST') options.headers = {{...options.headers, 'X-Compute-Token': '{CLOUD_REQUEST_TOKEN}'}};
  const response = await fetch(path, options);
  const data = await response.json();
  if (response.status === 403 && data.code === 'stale_ui_session') {{
    const key = 'voxadub-ui-session-refresh-token';
    if (sessionStorage.getItem(key) !== '{CLOUD_REQUEST_TOKEN}') {{
      sessionStorage.setItem(key, '{CLOUD_REQUEST_TOKEN}');
      window.location.replace('/');
      throw new Error('This page belonged to an older application session. The page was refreshed; retry the action.');
    }}
  }}
  if (!response.ok) throw new Error(data.error || data.message || 'Request failed');
  return data;
}}

function setStatus(text) {{
  const el = document.getElementById('status');
  el.textContent = text;
  el.className = 'badge' + (text === 'Done' ? ' ok' : text === 'Failed' ? ' bad' : '');
}}

function showError(error) {{
  const message = error && error.message ? error.message : String(error);
  activityErrorVersion++; activity(message, 'error'); currentActivity = '';
  setStatus('Failed');
  document.getElementById('console').textContent += `\\n${{message}}\\n`;
}}

async function refresh() {{
  const data = await api('/api/status');
  uploadedVideoPath = data.uploadedVideo || '';
  if (currentActivity === 'job') {{
    if (data.running) {{ activitySeenRunning = true; activity(data.status + ' — see Process Log for details.'); }}
    else if (activitySeenRunning && ['Done','Failed','Cancelled'].includes(data.status)) {{
      activity(data.status + ' — see Process Log for details.', data.status === 'Failed' ? 'error' : 'success'); currentActivity = '';
    }}
  }}
  setStatus(data.status);
  document.getElementById('console').textContent = data.logs || 'Ready.';
  const provider = document.getElementById('batchProvider').value;
  if (['colab', 'modal'].includes(provider)) {{
    document.getElementById('modelHint').textContent = 'Connect & prepare GPU installs missing models on the selected cloud provider. No AI weights are downloaded to this computer.';
    document.getElementById('translationModelHint').textContent = 'The chosen translation model is prepared on your cloud GPU.';
  }} else {{
    document.getElementById('modelHint').textContent = data.modelInstalled
      ? `Vocal model ready (${{data.modelSizeMb}} MB); VoxCPM2 ${{provider === 'modal-tts' ? 'runs on Modal' : data.voxcpmModelInstalled ? 'ready' : 'not installed'}}.`
      : 'Vocal model not installed; VoxCPM2 ' + (provider === 'modal-tts' ? 'runs on Modal.' : data.voxcpmModelInstalled ? 'ready.' : 'not installed.');
    const selectedModel = document.getElementById('translationModel').value;
    const selectedStatus = data.translationModels.find(item => item.key === selectedModel);
    document.getElementById('translationModelHint').textContent = selectedStatus
      ? `${{selectedStatus.installed ? 'Installed' : 'Not installed'}} · ~${{selectedStatus.approximate_download_gb}} GB · runtime ${{selectedStatus.runtime_ready ? 'ready' : 'not ready'}}`
      : 'Unknown model.';
  }}
  document.getElementById('latentSyncHint').textContent = data.latentSyncReady
    ? 'LatentSync files ready.'
    : 'Optional: not installed yet.';
  document.getElementById('diarizationHint').textContent = ['colab','modal'].includes(provider)
    ? 'Cloud speaker tracking uses its cached Community-1 model, or the optional Hugging Face setup token. Local model status does not apply.' : data.diarization.detail;
  document.getElementById('videoHint').textContent = data.uploadedVideo || 'No video uploaded yet.';
  document.getElementById('startButton').disabled = data.running;
  await refreshCloud();

  const preview = document.getElementById('preview');
  const video = document.getElementById('finalVideo');
  const link = document.getElementById('videoLink');
  if (data.outputVideoUrl) {{
    const url = data.outputVideoUrl;
    preview.classList.add('active');
    if (currentVideoUrl !== url) {{
      currentVideoUrl = url;
      video.pause();
      video.removeAttribute('src');
      video.load();
    }}
  }} else {{
    preview.classList.remove('active');
    link.href = '#';
    document.getElementById('downloadLink').href = '#';
    if (currentVideoUrl) {{
      currentVideoUrl = '';
      video.removeAttribute('src');
      video.load();
    }}
  }}
}}

async function authorizeOutputAccess() {{
  const response = await api('/api/output-access', {{ method: 'POST' }});
  if (!response.url) throw new Error('The finished output is no longer available.');
  return response.url;
}}

document.getElementById('videoLink').addEventListener('click', async event => {{
  event.preventDefault();
  if (!currentVideoUrl) return;
  try {{
    const url = await authorizeOutputAccess();
    const video = document.getElementById('finalVideo');
    video.src = url;
    video.load();
    video.focus();
  }} catch (error) {{ showError(error); }}
}});

document.getElementById('downloadLink').addEventListener('click', async event => {{
  event.preventDefault();
  if (!currentVideoUrl) return;
  try {{
    const url = await authorizeOutputAccess();
    const download = document.createElement('a');
    download.href = url + (url.includes('?') ? '&' : '?') + 'download=1';
    download.rel = 'noreferrer';
    document.body.appendChild(download);
    download.click();
    download.remove();
  }} catch (error) {{ showError(error); }}
}});

async function uploadVideo() {{
  const input = document.getElementById('video');
  if (!input.files.length) throw new Error('Choose a video first.');
  const form = new FormData();
  form.append('video', input.files[0]);
  setStatus('Uploading');
  const data = await api('/api/upload', {{ method: 'POST', body: form }});
  uploadedFile = input.files[0];
  await refresh();
  return data.path;
}}

async function downloadModel() {{
  try {{
    await api('/api/download-model', {{ method: 'POST' }});
    await refresh();
  }} catch (error) {{
    showError(error);
  }}
}}

async function setupLatentSync() {{
  try {{
    await api('/api/setup-latentsync', {{ method: 'POST' }});
    await refresh();
  }} catch (error) {{
    showError(error);
  }}
}}

async function setupHyMT2() {{
  try {{ await api('/api/setup-hymt2', {{ method: 'POST' }}); await refresh(); }}
  catch (error) {{ showError(error); }}
}}

async function downloadHyMT2() {{
  try {{
    const model = document.getElementById('translationModel').value;
    await api('/api/download-hymt2', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ model }})
    }});
    await refresh();
  }} catch (error) {{ showError(error); }}
}}

async function setupDiarization() {{
  try {{ await api('/api/setup-diarization', {{ method: 'POST' }}); await refresh(); }}
  catch (error) {{ showError(error); }}
}}

async function startDubbing() {{
  try {{
    await saveComputeSettings();
    const input = document.getElementById('video');
    if (!uploadedVideoPath || (input.files.length && input.files[0] !== uploadedFile)) {{
      if (!input.files.length) {{
        throw new Error('Choose a video first.');
      }}
      await uploadVideo();
    }}

    const provider = document.getElementById('batchProvider').value;
    if (['colab','modal'].includes(provider) && !(cloudState.stage === 'ready' && cloudState.provider === provider && cloudState.model === document.getElementById('translationModel').value)) {{
      pendingDubbing = true;
      if (cloudState.provider === provider && ['connecting','preparing','authorizing'].includes(cloudState.stage)) return;
      await connectCloud();
      return;
    }}

    const source = document.getElementById('source').value;
    const target = document.getElementById('target').value;
    const lipSync = document.getElementById('lipSync').checked;
    const diarization = document.getElementById('diarization').checked;
    const numSpeakers = document.getElementById('numSpeakers').value || null;
    const bounds = document.getElementById('speakerBounds').value.trim();
    let minSpeakers = null, maxSpeakers = null;
    if (bounds) {{
      const parts = bounds.split(',').map(value => value.trim());
      if (parts.length !== 2) throw new Error('Speaker bounds must look like 2,5.');
      [minSpeakers, maxSpeakers] = parts;
    }}
    const translationModel = document.getElementById('translationModel').value;
    const translationDevice = document.getElementById('translationDevice').value;
    const glossary = document.getElementById('glossary').value.trim();
    await api('/api/run', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ source, target, lipSync, diarization, numSpeakers,
        minSpeakers, maxSpeakers, translationModel, translationDevice, glossary,
        batchProvider: document.getElementById('batchProvider').value,
        liveProvider: document.getElementById('liveProvider').value,
        colabSession: document.getElementById('colabSession').value.trim(),
        remoteProject: document.getElementById('remoteProject').value.trim() }})
    }});
    await refresh();
  }} catch (error) {{
    showError(error);
  }}
}}

async function cancelRun() {{
  try {{
    await api('/api/cancel', {{ method: 'POST' }});
    await refresh();
  }} catch (error) {{
    showError(error);
  }}
}}

async function openOutput() {{
  try {{
    await api('/api/open-output', {{ method: 'POST' }});
  }} catch (error) {{
    showError(error);
  }}
}}
let pollingFailed = false;
let pollingInProgress = false;
async function pollSafely() {{
  if (pollingInProgress) return;
  pollingInProgress = true;
  try {{
    await refresh();
    if (pollingFailed) {{ pollingFailed = false; activity('Status connection restored.', 'success'); }}
  }} catch (_) {{
    if (!pollingFailed) {{ pollingFailed = true; activity('Unable to refresh status. Check the application and reload this page.', 'error'); }}
  }} finally {{ pollingInProgress = false; }}
}}
setInterval(pollSafely, 1000);
pollSafely();
updateComputeHint();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/new-ui-session":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                json_response(self, {"error": "Local application access required."}, 403)
                return
            reset = reset_gui_view_state()
            message = "Dashboard view reset." if reset else "A running job is active; its current status was kept."
            json_response(self, {"ok": reset, "message": message})
        elif parsed.path == "/":
            body = page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/live":
            try:
                location = launch_live_service()
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
            except RuntimeError as exc:
                json_response(self, {"error": str(exc)}, 503)
        elif parsed.path == "/live/browser":
            # Legacy launch URL: the dashboard now starts the browser through
            # POST /api/open-live-browser and reports progress in place. Never
            # leave users on a status page that looks like the browser itself.
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
        elif parsed.path == "/api/live-browser-status":
            try:
                value = json.loads(LIVE_BROWSER_STATUS.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                value = {"state": "idle", "message": "No browser launch has been requested."}
            from scripts.open_live_dubbing_browser import live_browser_profile_in_use
            value["profileInUse"] = live_browser_profile_in_use()
            if value["profileInUse"] and value.get("state") in ("idle", "closed", "error"):
                value.update(
                    state="ready",
                    message="Google Chrome is already open with the Live Dubbing extension. Switch to that window.",
                )
            json_response(self, value)
        elif parsed.path == "/media/output-video":
            self.handle_video()
        elif parsed.path == "/api/status":
            with state_lock:
                logs = "".join(state["logs"])
                status = state["status"]
                running = state["process"] is not None
                uploaded_video = state["uploaded_video"]
                output_video = state["output_video"]
            output_video_path = Path(output_video) if output_video else None
            output_video_exists = bool(output_video_path and output_video_path.exists())
            output_video_url = ""
            if output_video_exists:
                output_video_url = f"/media/output-video?v={int(output_video_path.stat().st_mtime)}"
            voxcpm_ready, _ = model_ready()
            json_response(
                self,
                {
                    "status": status,
                    "running": running,
                    "logs": logs,
                    "uploadedVideo": Path(uploaded_video).name if uploaded_video else "",
                    "outputVideo": output_video_path.name if output_video_exists else "",
                    "outputVideoUrl": output_video_url,
                    "modelInstalled": MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0,
                    "modelSizeMb": round(MODEL_PATH.stat().st_size / (1024 * 1024), 1) if MODEL_PATH.exists() else 0,
                    "voxcpmModelInstalled": voxcpm_ready,
                    "translationModels": hymt2_model_status(),
                    "diarization": {
                        "installed": diarization_status().installed,
                        "runtimeReady": diarization_status().runtime_ready,
                        "detail": diarization_status().detail,
                    },
                    "latentSyncReady": latentsync_files_ready(),
                    "computeSettings": load_compute_settings(),
                },
            )
        elif parsed.path == "/api/compute-settings":
            json_response(self, load_compute_settings())
        else:
            json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        token = self.headers.get("X-Compute-Token", "")
        origin = self.headers.get("Origin")
        if not secrets.compare_digest(token, CLOUD_REQUEST_TOKEN):
            json_response(self, {
                "error": "Refresh this local GUI before continuing.",
                "code": "stale_ui_session",
            }, 403)
            return
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            json_response(self, {"error": "This request came from an unexpected origin.", "code": "invalid_origin"}, 403)
            return
        if parsed.path.startswith("/api/cloud-"):
            self.handle_cloud(parsed.path)
        elif parsed.path == "/api/upload":
            self.handle_upload()
        elif parsed.path == "/api/download-model":
            ok, message = run_process(
                [sys.executable, str(BASE_DIR / "scripts" / "setup_models.py")],
                "Downloading models",
            )
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/setup-latentsync":
            ok, message = run_process([sys.executable, str(BASE_DIR / "scripts" / "setup_latentsync.py")], "Setting up LatentSync")
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/setup-hymt2":
            ok, message = run_process(
                [sys.executable, str(BASE_DIR / "scripts" / "setup_hymt2.py")],
                "Setting up Hy-MT2 runtime",
            )
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/download-hymt2":
            self.handle_hymt2_download()
        elif parsed.path == "/api/setup-diarization":
            ok, message = run_process(
                [
                    sys.executable,
                    str(BASE_DIR / "scripts" / "setup_diarization.py"),
                    "--download-model",
                ],
                "Setting up speaker diarization",
            )
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/run":
            self.handle_run()
        elif parsed.path == "/api/compute-settings":
            self.handle_compute_settings()
        elif parsed.path == "/api/open-live-browser":
            try:
                started, message = launch_live_browser()
                json_response(self, {"ok": True, "started": started, "message": message}, 202 if started else 200)
            except (OSError, RuntimeError) as exc:
                json_response(self, {"error": str(exc)}, 503)
        elif parsed.path == "/api/cancel":
            with state_lock:
                process = state["process"]
            if process is not None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    )
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                add_log("\nCancel requested.\n")
            json_response(self, {"ok": True})
        elif parsed.path == "/api/open-output":
            result = open_output_folder(OUTPUT_DIR)
            json_response(self, result, 200 if result["ok"] else 503)
        elif parsed.path == "/api/output-access":
            if not origin:
                json_response(
                    self,
                    {"error": "Output access must be authorized by the local GUI."},
                    403,
                    headers={"Cache-Control": "no-store"},
                )
                return
            self.handle_output_access()
        else:
            json_response(self, {"error": "Not found"}, 404)

    def handle_cloud(self, path):
        token = self.headers.get("X-Compute-Token", "")
        if not secrets.compare_digest(token, CLOUD_REQUEST_TOKEN):
            json_response(self, {"error": "Refresh this local GUI before connecting."}, 403)
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            json_response(self, {"error": "Cloud controls require the same local GUI origin."}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 16384:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(size) or "{}")
            cloud_connection.touch()
            if path == "/api/cloud-status":
                json_response(self, cloud_connection.status())
                return
            if path == "/api/cloud-input":
                cloud_connection.reply(payload.get("value", ""))
            elif path == "/api/cloud-connect":
                with state_lock:
                    if state["process"] is not None:
                        raise ValueError("Wait for the current task to finish before connecting another GPU.")
                cloud_connection.start(payload.get("provider"), model=payload.get("model", "hymt2-1.8b"),
                                       temporary=bool(payload.get("temporary")), live=bool(payload.get("live")),
                                       diarization=bool(payload.get("diarization")), lip_sync=bool(payload.get("lipSync")),
                                       hf_token=str(payload.get("hfToken", "")))
            elif path == "/api/cloud-disconnect":
                def release():
                    cancel_active_job()
                    try:
                        cloud_connection.disconnect()
                    except Exception:
                        with cloud_connection.lock:
                            cloud_connection.data.update(stage="error", message="Could not confirm cloud shutdown. Retry Disconnect GPU.")
                threading.Thread(target=release, daemon=True).start()
            else:
                json_response(self, {"error": "Not found"}, 404)
                return
            json_response(self, {"ok": True})
        except (ValueError, TypeError, OSError) as exc:
            json_response(self, {"error": str(exc)}, 400)

    def handle_upload(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                raise ValueError("Upload request is empty or incomplete.")
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(content_length)},
            )
            file_item = form["video"] if "video" in form else None
            if file_item is None or not file_item.filename:
                raise ValueError("No video was included in the upload.")
            if getattr(form, "done", 0) == -1 or getattr(file_item, "done", 0) == -1:
                raise ValueError("Upload ended before the complete video was received.")
            expected_size = getattr(file_item, "length", -1)
            if expected_size < 0:
                expected_size = None
            output_path = save_uploaded_file(
                file_item.file, file_item.filename, UPLOAD_DIR, expected_size=expected_size
            )
        except Exception as exc:
            add_log(
                sanitize_for_display(
                    f"Upload failed during multipart validation: {type(exc).__name__}: {exc}"
                )
                + "\n"
            )
            message = (
                sanitize_for_display(str(exc))
                if isinstance(exc, (ValueError, TypeError, OSError, EOFError))
                else "Upload could not be completed. Select the video again and retry."
            )
            json_response(self, {"error": message}, 400)
            return

        with state_lock:
            state["uploaded_video"] = str(output_path)
            state["status"] = "Video uploaded"
        add_log(f"Uploaded video: {sanitize_for_display(output_path.name)}\n")
        json_response(self, {"ok": True, "filename": output_path.name})

    def handle_hymt2_download(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length) or "{}")
        model = payload.get("model")
        valid_models = {item.key for item in HYMT2_MODELS}
        if model not in valid_models:
            json_response(self, {"error": "Choose a valid Hy-MT2 model."}, 400)
            return
        ok, message = run_process(
            [
                sys.executable,
                str(BASE_DIR / "scripts" / "manage_hymt2_models.py"),
                "download",
                model,
            ],
            f"Downloading {model}",
        )
        json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)

    def handle_output_access(self):
        """Grant one browser access window to the current output video."""
        with state_lock:
            output_video = state["output_video"]
        if not output_video:
            json_response(self, {"error": "No output video is available."}, 404)
            return

        try:
            output_root = OUTPUT_DIR.resolve(strict=True)
            video_path = Path(output_video).resolve(strict=True)
            video_path.relative_to(output_root)
            if not video_path.is_file():
                raise ValueError("not a regular file")
        except (OSError, RuntimeError, ValueError):
            json_response(self, {"error": "The output video is unavailable."}, 403)
            return

        token = secrets.token_urlsafe(32)
        expires_at = time.time() + OUTPUT_ACCESS_TTL_SECONDS
        with output_access_lock:
            output_access_grant.update(
                token=token,
                path=str(video_path),
                expires_at=expires_at,
            )

        json_response(
            self,
            {
                "ok": True,
                "url": "/media/output-video",
                "filename": video_path.name,
                "expiresIn": OUTPUT_ACCESS_TTL_SECONDS,
            },
            headers={
                "Set-Cookie": (
                    f"{OUTPUT_ACCESS_COOKIE}={token}; HttpOnly; SameSite=Strict; "
                    f"Path=/media/output-video; Max-Age={OUTPUT_ACCESS_TTL_SECONDS}"
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def handle_video(self):
        with state_lock:
            output_video = state["output_video"]
        if not output_video:
            json_response(self, {"error": "No output video yet"}, 404)
            return

        try:
            output_root = OUTPUT_DIR.resolve(strict=True)
            video_path = Path(output_video).resolve(strict=True)
        except (OSError, RuntimeError):
            json_response(self, {"error": "Output video not found"}, 404)
            return
        try:
            video_path.relative_to(output_root)
        except ValueError:
            json_response(self, {"error": "Invalid output video path"}, 403)
            return
        if not video_path.is_file():
            json_response(self, {"error": "Output video not found"}, 404)
            return

        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            cookie_token = cookie[OUTPUT_ACCESS_COOKIE].value
        except (KeyError, CookieError):
            cookie_token = ""
        with output_access_lock:
            grant = dict(output_access_grant)
        authorized = (
            bool(cookie_token)
            and bool(grant["token"])
            and secrets.compare_digest(cookie_token, grant["token"])
            and grant["expires_at"] > time.time()
            and secrets.compare_digest(str(video_path), grant["path"])
        )
        if not authorized:
            json_response(
                self,
                {"error": "Authorize this output from the local GUI before viewing or downloading it."},
                403,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
            return

        file_size = video_path.stat().st_size
        range_header = self.headers.get("Range")
        content_type = mimetypes.guess_type(str(video_path))[0] or "video/mp4"

        start = 0
        end = file_size - 1
        status_code = 200
        if range_header and range_header.startswith("bytes="):
            status_code = 206
            try:
                range_value = range_header.split("=", 1)[1].split(",", 1)[0]
                start_text, end_text = range_value.split("-", 1)
                if start_text:
                    start = int(start_text)
                    if end_text:
                        end = int(end_text)
                else:
                    suffix = int(end_text)
                    if suffix <= 0:
                        raise ValueError("Invalid suffix")
                    start = max(0, file_size - suffix)
                end = min(end, file_size - 1)
            except ValueError:
                start, end = file_size, -1

        if start < 0 or end < start or start >= file_size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        chunk_size = end - start + 1
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "private, no-store")
        if urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("download") == ["1"]:
            self.send_header("Content-Disposition", "attachment; filename=\"dubbed_video.mp4\"; filename*=UTF-8''" + urllib.parse.quote(video_path.name))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(chunk_size))
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        with video_path.open("rb") as video_file:
            video_file.seek(start)
            remaining = chunk_size
            try:
                while remaining > 0:
                    chunk = video_file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    def handle_run(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length) or "{}")
        source = payload.get("source") or "automatic"
        target = payload.get("target") or "en"
        lip_sync = bool(payload.get("lipSync"))
        use_diarization = bool(payload.get("diarization", True))
        translation_model = payload.get("translationModel") or "hymt2-1.8b"
        translation_device = payload.get("translationDevice") or "auto"
        old_settings = load_compute_settings()
        try:
            save_compute_settings({
                "batch_provider": payload.get("batchProvider", old_settings["batch_provider"]),
                "live_provider": payload.get("liveProvider", old_settings["live_provider"]),
                "colab_session": payload.get("colabSession", old_settings["colab_session"]),
                "remote_project": payload.get("remoteProject", old_settings["remote_project"]),
            })
            preferences = load_compute_settings()
            provider = validate_provider(payload.get("batchProvider", preferences["batch_provider"]))
        except (ValueError, OSError) as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        source_arg = "auto" if source == "automatic" else source
        glossary_text = str(payload.get("glossary") or "").strip()
        glossary_payload = None
        if glossary_text:
            try:
                glossary_payload = json.loads(glossary_text)
                if not isinstance(glossary_payload, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                json_response(self, {"error": "Glossary must be a JSON object."}, 400)
                return

        with state_lock:
            uploaded_video = state["uploaded_video"]
        if not uploaded_video:
            json_response(self, {"error": "Upload a video first."}, 400)
            return
        if provider not in ("colab", "modal") and not MODEL_PATH.exists():
            json_response(self, {"error": "Download the required models first."}, 400)
            return
        voxcpm_ready, _ = model_ready() if provider == "local" else (True, "Selected remote TTS provider")
        if not voxcpm_ready:
            json_response(self, {"error": "Download the VoxCPM2 model first."}, 400)
            return
        if lip_sync and provider not in ("colab", "modal") and not latentsync_files_ready():
            json_response(
                self,
                {"error": "LatentSync files are missing. Click `Setup / Check LatentSync` first."},
                400,
            )
            return
        selected = next(
            (item for item in hymt2_model_status() if item["key"] == translation_model),
            None,
        )
        if provider not in ("colab", "modal") and (selected is None or not selected["installed"] or not selected["runtime_ready"]):
            json_response(
                self,
                {"error": "Set up the Hy-MT2 runtime and download the selected model first."},
                400,
            )
            return

        if provider in ("colab", "modal"):
            ready = cloud_connection.status()
            if ready["stage"] != "ready" or ready["provider"] != provider or ready.get("model") != translation_model:
                json_response(self, {"error": "Click Connect & prepare GPU for the selected provider and model first."}, 409)
                return
            command = [
                sys.executable, str(BASE_DIR / "scripts" / "run_dubbing.py"), uploaded_video,
                "--compute", provider,
                "--session", preferences["colab_session"],
                "--remote-project", preferences["remote_project"],
                "--output", str(expected_output_path(uploaded_video, lip_sync=lip_sync, provider=provider)),
                "--source-language", source_arg, "--target-language", target,
                "--translation-model", translation_model, "--translation-device", translation_device,
            ]
        else:
            command = [
                sys.executable,
                str(BASE_DIR / "scripts" / "run_pipeline.py"),
                uploaded_video,
                "--source-language",
                source_arg,
                "--target-language",
                target,
                "--translation-model",
                translation_model,
                "--translation-device",
                translation_device,
                "--tts-backend",
                "modal" if provider == "modal-tts" else "native",
            ]
        if not use_diarization:
            command.append("--no-diarization")
        for option, field in (
            ("--num-speakers", "numSpeakers"),
            ("--min-speakers", "minSpeakers"),
            ("--max-speakers", "maxSpeakers"),
        ):
            value = payload.get(field)
            if value not in (None, ""):
                command.extend([option, str(value)])
        if glossary_text:
            glossary_path = UPLOAD_DIR / "glossary.json"
            glossary_path.write_text(
                json.dumps(glossary_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            command.extend(["--glossary", str(glossary_path)])
        if lip_sync:
            command.append("--lip-sync")
        output_path = expected_output_path(uploaded_video, lip_sync=lip_sync, provider=provider)
        ok, message = run_process(command, "Running", output_path=output_path)
        json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)

    def handle_compute_settings(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length) or "{}")
        try:
            save_compute_settings({
                "batch_provider": payload.get("batch_provider", "local"),
                "live_provider": payload.get("live_provider", "local"),
                "colab_session": payload.get("colab_session", ""),
                "remote_project": payload.get("remote_project", "/content/video-dubbing-translator-main"),
            })
            json_response(self, {"ok": True, "settings": load_compute_settings()})
        except (ValueError, OSError) as exc:
            json_response(self, {"error": str(exc)}, 400)

    def log_message(self, format, *args):
        return


def port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    UPLOAD_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    port = 8765
    if port_is_open(port):
        url = f"http://127.0.0.1:{port}"
        print(f"VoxaDub GUI is already running: {url}")
        return

    server = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    url = f"http://127.0.0.1:{port}"
    print(f"VoxaDub GUI: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nVoxaDub GUI stopped.")
    finally:
        server.server_close()
        stop_managed_service()


if __name__ == "__main__":
    main()
