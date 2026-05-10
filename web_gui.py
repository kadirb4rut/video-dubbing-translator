import cgi
import html
import json
import mimetypes
import os
import queue
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from Video_Translator import LANGUAGES


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


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def add_log(line):
    with state_lock:
        state["logs"].append(line)
        state["logs"] = state["logs"][-1200:]


def expected_output_path(uploaded_video, lip_sync=False):
    video_path = Path(uploaded_video)
    if lip_sync:
        return OUTPUT_DIR / f"{video_path.stem}_lipsync{video_path.suffix or '.mp4'}"
    return OUTPUT_DIR / video_path.name


def latentsync_files_ready():
    return all(path.exists() for path in LATENTSYNC_CHECKPOINTS)


def run_process(command, status, output_path=None):
    with state_lock:
        if state["process"] is not None:
            return False, "Another process is already running."
        state["status"] = status
        if output_path is not None:
            state["output_video"] = ""
        state["logs"] = ["$ " + " ".join(str(part) for part in command) + "\n\n"]

    thread = threading.Thread(target=worker, args=(command, output_path), daemon=True)
    thread.start()
    return True, "Started."


def worker(command, output_path=None):
    try:
        process = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with state_lock:
            state["process"] = process

        for line in process.stdout:
            add_log(line)

        return_code = process.wait()
        add_log("\nDone.\n" if return_code == 0 else f"\nProcess failed with exit code {return_code}.\n")
        with state_lock:
            state["status"] = "Done" if return_code == 0 else "Failed"
            if return_code == 0 and output_path and Path(output_path).exists():
                state["output_video"] = str(output_path)
    except Exception as exc:
        add_log(f"\n{type(exc).__name__}: {exc}\n")
        with state_lock:
            state["status"] = "Failed"
    finally:
        with state_lock:
            state["process"] = None


def json_response(handler, data, status=200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def page():
    source_options = "\n".join(
        f'<option value="{html.escape(code)}">{html.escape(name)}</option>'
        for name, code in LANGUAGES.items()
    )
    target_options = "\n".join(
        f'<option value="{html.escape(code)}" {"selected" if code == "en" else ""}>{html.escape(name)}</option>'
        for name, code in LANGUAGES.items()
        if code != "automatic"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video Dubbing Translator</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #65758b;
      --line: #d7e0ea;
      --line-strong: #c4ceda;
      --blue: #2367d1;
      --blue-dark: #1b55af;
      --green: #16803c;
      --red: #b42318;
      --console: #0e1621;
      --soft: #f8fafc;
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      overflow-x: hidden;
    }}
    main {{ max-width: 1040px; margin: 0 auto; padding: 22px 28px; }}
    header {{ margin-bottom: 14px; }}
    h1 {{ margin: 0 0 4px; font-size: 26px; line-height: 1.08; letter-spacing: 0; }}
    .subtitle {{ color: var(--muted); font-size: 14px; }}
    .layout {{ display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; align-items: start; }}
    .layout, .panel, .step, .row > div {{ min-width: 0; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.045);
    }}
    .step {{ margin-bottom: 14px; }}
    .step h2 {{ margin: 0 0 9px; font-size: 14px; line-height: 1.25; }}
    label {{ display: block; margin: 0 0 6px; color: #334155; font-weight: 650; font-size: 13px; }}
    input[type=file], select {{
      width: 100%;
      max-width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 34px;
      padding: 6px 10px;
      font: inherit;
      background: #fff;
    }}
    input[type=file]::file-selector-button {{
      border: 1px solid var(--line-strong);
      border-radius: 5px;
      padding: 5px 9px;
      margin-right: 8px;
      background: #f1f5f9;
      color: #172033;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }}
    select {{ appearance: auto; }}
    button {{
      width: 100%;
      border: 0;
      border-radius: 6px;
      min-height: 32px;
      padding: 7px 12px;
      font: inherit;
      font-size: 13px;
      font-weight: 750;
      cursor: pointer;
      background: #e7edf5;
      color: #17202a;
    }}
    button.primary {{ background: var(--blue); color: white; }}
    button.primary:hover {{ background: var(--blue-dark); }}
    button.secondary:hover {{ background: #cbd5e1; }}
    button.danger {{ background: var(--red); color: white; }}
    button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .hint {{ color: var(--muted); font-size: 12px; line-height: 1.3; margin-top: 6px; overflow-wrap: anywhere; word-break: break-word; }}
    .output-hint {{
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .statusbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }}
    .statusbar h2 {{ color: #111827; }}
    .badge {{ border-radius: 999px; padding: 5px 9px; background: #dbeafe; color: #1e3a8a; font-weight: 800; font-size: 12px; }}
    .badge.ok {{ background: #dcfce7; color: #166534; }}
    .badge.bad {{ background: #fee2e2; color: #991b1b; }}
    .console {{
      height: 280px;
      overflow: auto;
      background: var(--console);
      color: #e8eef5;
      border-radius: 8px;
      padding: 13px;
      font: 12px/1.45 Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .checkline {{
      display: flex;
      align-items: center;
      gap: 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: var(--soft);
      color: #1f2937;
      font-weight: 700;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .checkline input {{ width: 16px; height: 16px; flex: 0 0 auto; }}
    .preview {{ display: none; margin-bottom: 16px; }}
    .preview.active {{ display: block; }}
    .preview video {{
      display: none;
      width: 100%;
      max-height: 280px;
      aspect-ratio: 16 / 9;
      border-radius: 8px;
      background: #0f172a;
    }}
    .preview.active video {{ display: block; }}
    @media (max-width: 900px) {{
      main {{ padding: 16px; }}
      .layout {{ grid-template-columns: 1fr; }}
      .console {{ height: 260px; }}
    }}
    @media (max-width: 600px) {{
      body {{ font-size: 15px; }}
      main {{ padding: 14px; }}
      header {{ margin-bottom: 12px; }}
      h1 {{ font-size: 24px; }}
      .subtitle {{ font-size: 13px; line-height: 1.35; }}
      .panel {{ padding: 14px; }}
      .row, .actions {{ grid-template-columns: 1fr; }}
      input[type=file], select, button {{ min-height: 44px; }}
      .checkline {{ min-height: 44px; }}
      .preview video {{ max-height: 240px; }}
      .console {{ height: 230px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Video Dubbing Translator</h1>
    <div class="subtitle">Upload a video, choose languages, and generate a dubbed version with live progress logs.</div>
  </header>
  <section class="layout">
    <aside class="panel">
      <div class="step">
        <h2>1. Video</h2>
        <input id="video" type="file" accept="video/*">
        <button class="secondary" style="margin-top:10px" onclick="uploadVideo()">Upload Selected Video</button>
        <div id="videoHint" class="hint">No video uploaded yet.</div>
      </div>
      <div class="step">
        <h2>2. Languages</h2>
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
        <h2>3. Models</h2>
        <button class="secondary" onclick="downloadModel()">Download / Check Dubbing Model</button>
        <div id="modelHint" class="hint">Checking model...</div>
        <button class="secondary" style="margin-top:10px" onclick="setupLatentSync()">Setup / Check LatentSync</button>
        <div id="latentSyncHint" class="hint">Checking LatentSync...</div>
      </div>
      <div class="step">
        <h2>4. Lip-sync</h2>
        <label class="checkline" for="lipSync">
          <input id="lipSync" type="checkbox">
          Apply LatentSync after dubbing
        </label>
        <div class="hint">Requires a separate LatentSync setup and an NVIDIA CUDA GPU.</div>
      </div>
      <div class="step">
        <h2>5. Generate</h2>
        <div class="actions">
          <button id="startButton" class="primary" onclick="startDubbing()">Start Dubbing</button>
          <button class="danger" onclick="cancelRun()">Cancel</button>
        </div>
      </div>
      <button class="secondary" onclick="openOutput()">Open Output Folder</button>
      <div class="hint output-hint" title="{html.escape(str(OUTPUT_DIR))}">Output: {html.escape(str(OUTPUT_DIR))}</div>
    </aside>
    <section class="panel">
      <div id="preview" class="preview">
        <div class="statusbar">
          <h2 style="margin:0;font-size:18px">Final Video</h2>
          <a id="videoLink" class="hint" href="#" target="_blank" rel="noreferrer">Open video</a>
        </div>
        <video id="finalVideo" controls playsinline preload="metadata"></video>
      </div>
      <div class="statusbar">
        <h2 style="margin:0;font-size:18px">Process Log</h2>
        <span id="status" class="badge">Ready</span>
      </div>
      <div id="console" class="console">Ready. Upload a video, download the model once, then start dubbing.</div>
    </section>
  </section>
</main>
<script>
let uploadedVideoPath = '';
let currentVideoUrl = '';

async function api(path, options = {{}}) {{
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}}

function setStatus(text) {{
  const el = document.getElementById('status');
  el.textContent = text;
  el.className = 'badge' + (text === 'Done' ? ' ok' : text === 'Failed' ? ' bad' : '');
}}

function showError(error) {{
  const message = error && error.message ? error.message : String(error);
  setStatus('Failed');
  document.getElementById('console').textContent += `\\n${{message}}\\n`;
  alert(message);
}}

async function refresh() {{
  const data = await api('/api/status');
  uploadedVideoPath = data.uploadedVideo || '';
  setStatus(data.status);
  document.getElementById('console').textContent = data.logs || 'Ready.';
  document.getElementById('modelHint').textContent = data.modelInstalled
    ? `Installed: baseline.pth (${{data.modelSizeMb}} MB)`
    : 'Not installed yet.';
  document.getElementById('latentSyncHint').textContent = data.latentSyncReady
    ? 'LatentSync files ready.'
    : 'Optional: not installed yet.';
  document.getElementById('videoHint').textContent = data.uploadedVideo || 'No video uploaded yet.';
  document.getElementById('startButton').disabled = data.running;

  const preview = document.getElementById('preview');
  const video = document.getElementById('finalVideo');
  const link = document.getElementById('videoLink');
  if (data.outputVideoUrl) {{
    const url = data.outputVideoUrl;
    preview.classList.add('active');
    link.href = url;
    if (currentVideoUrl !== url) {{
      currentVideoUrl = url;
      video.src = url;
      video.load();
    }}
  }} else {{
    preview.classList.remove('active');
    link.href = '#';
    if (currentVideoUrl) {{
      currentVideoUrl = '';
      video.removeAttribute('src');
      video.load();
    }}
  }}
}}

async function uploadVideo() {{
  const input = document.getElementById('video');
  if (!input.files.length) return alert('Choose a video first.');
  const form = new FormData();
  form.append('video', input.files[0]);
  setStatus('Uploading');
  const response = await fetch('/api/upload', {{ method: 'POST', body: form }});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Upload failed');
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

async function startDubbing() {{
  try {{
    const input = document.getElementById('video');
    if (!uploadedVideoPath) {{
      if (!input.files.length) {{
        throw new Error('Choose a video first.');
      }}
      await uploadVideo();
    }}

    const source = document.getElementById('source').value;
    const target = document.getElementById('target').value;
    const lipSync = document.getElementById('lipSync').checked;
    await api('/api/run', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ source, target, lipSync }})
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
setInterval(refresh, 1000);
refresh();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
            json_response(
                self,
                {
                    "status": status,
                    "running": running,
                    "logs": logs,
                    "uploadedVideo": uploaded_video,
                    "outputVideo": output_video if output_video_exists else "",
                    "outputVideoUrl": output_video_url,
                    "modelInstalled": MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0,
                    "modelSizeMb": round(MODEL_PATH.stat().st_size / (1024 * 1024), 1) if MODEL_PATH.exists() else 0,
                    "latentSyncReady": latentsync_files_ready(),
                },
            )
        else:
            json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/upload":
            self.handle_upload()
        elif parsed.path == "/api/download-model":
            ok, message = run_process([sys.executable, str(BASE_DIR / "scripts" / "download_model.py")], "Downloading model")
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/setup-latentsync":
            ok, message = run_process([sys.executable, str(BASE_DIR / "scripts" / "setup_latentsync.py")], "Setting up LatentSync")
            json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)
        elif parsed.path == "/api/run":
            self.handle_run()
        elif parsed.path == "/api/cancel":
            with state_lock:
                process = state["process"]
            if process is not None:
                process.terminate()
                add_log("\nCancel requested.\n")
            json_response(self, {"ok": True})
        elif parsed.path == "/api/open-output":
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.run(["open", str(OUTPUT_DIR)], check=False)
            elif os.name == "nt":
                os.startfile(OUTPUT_DIR)
            else:
                subprocess.run(["xdg-open", str(OUTPUT_DIR)], check=False)
            json_response(self, {"ok": True})
        else:
            json_response(self, {"error": "Not found"}, 404)

    def handle_upload(self):
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})
        file_item = form["video"] if "video" in form else None
        if file_item is None or not file_item.filename:
            json_response(self, {"error": "No video uploaded"}, 400)
            return

        UPLOAD_DIR.mkdir(exist_ok=True)
        safe_name = Path(file_item.filename).name
        output_path = UPLOAD_DIR / safe_name
        with output_path.open("wb") as output:
            while True:
                chunk = file_item.file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)

        with state_lock:
            state["uploaded_video"] = str(output_path)
            state["status"] = "Video uploaded"
        add_log(f"Uploaded video: {output_path}\n")
        json_response(self, {"ok": True, "path": str(output_path)})

    def handle_video(self):
        with state_lock:
            output_video = state["output_video"]
        if not output_video:
            json_response(self, {"error": "No output video yet"}, 404)
            return

        video_path = Path(output_video).resolve()
        try:
            video_path.relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            json_response(self, {"error": "Invalid output video path"}, 403)
            return
        if not video_path.exists():
            json_response(self, {"error": "Output video not found"}, 404)
            return

        file_size = video_path.stat().st_size
        range_header = self.headers.get("Range")
        content_type = mimetypes.guess_type(str(video_path))[0] or "video/mp4"

        start = 0
        end = file_size - 1
        status_code = 200
        if range_header and range_header.startswith("bytes="):
            status_code = 206
            range_value = range_header.split("=", 1)[1].split(",", 1)[0]
            start_text, end_text = range_value.split("-", 1)
            if start_text:
                start = int(start_text)
            if end_text:
                end = int(end_text)
            end = min(end, file_size - 1)

        if start < 0 or end < start or start >= file_size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        chunk_size = end - start + 1
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
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
        source_arg = "auto" if source == "automatic" else source

        with state_lock:
            uploaded_video = state["uploaded_video"]
        if not uploaded_video:
            json_response(self, {"error": "Upload a video first."}, 400)
            return
        if not MODEL_PATH.exists():
            json_response(self, {"error": "Download the model first."}, 400)
            return
        if lip_sync and not latentsync_files_ready():
            json_response(
                self,
                {"error": "LatentSync files are missing. Click `Setup / Check LatentSync` first."},
                400,
            )
            return

        command = [
            sys.executable,
            str(BASE_DIR / "scripts" / "run_pipeline.py"),
            uploaded_video,
            "--source-language",
            source_arg,
            "--target-language",
            target,
        ]
        if lip_sync:
            command.append("--lip-sync")
        output_path = expected_output_path(uploaded_video, lip_sync=lip_sync)
        ok, message = run_process(command, "Running", output_path=output_path)
        json_response(self, {"ok": ok, "message": message}, 200 if ok else 409)

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
        print(f"Video Dubbing Translator GUI is already running: {url}")
        webbrowser.open(url)
        return

    server = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Video Dubbing Translator GUI: {url}")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
