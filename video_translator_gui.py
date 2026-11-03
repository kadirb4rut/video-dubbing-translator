import json
import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import END, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext, ttk

from Video_Translator import SOURCE_LANGUAGES, TARGET_LANGUAGES
from compute_backends.settings import load_compute_settings, save_compute_settings
from voxcpm_runtime import model_ready


BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "vocal-remover" / "models" / "baseline.pth"
OUTPUT_DIR = BASE_DIR / "vocal-remover" / "final_video" / "final"
LIVE_BROWSER_STATUS = BASE_DIR / "temp" / "live-dubbing-browser-status.json"
class VideoTranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Dubbing Translator")
        self.root.geometry("1040x680")
        self.root.minsize(920, 600)

        self.video_path = StringVar(value="")
        self.source_language = StringVar(value="Auto Detect")
        self.target_language = StringVar(value="English")
        self.status = StringVar(value="Ready")
        self.model_status = StringVar(value="")
        self.output_status = StringVar(value=str(OUTPUT_DIR))
        preferences = load_compute_settings()
        self.batch_provider_labels = {
            "This computer (CPU/GPU)": "local",
            "Modal GPU (VoxCPM2 speech only)": "modal-tts",
            "Modal (full pipeline; prepare in web GUI)": "modal",
        }
        self.live_provider_labels = {
            "This computer (CPU/GPU)": "local",
        }
        if os.name != "nt":
            self.batch_provider_labels["Colab CLI (disconnect in web GUI; macOS/Linux)"] = "colab"
            self.live_provider_labels["Colab CLI (disconnect in web GUI; macOS/Linux)"] = "colab"
        if os.name == "nt" and preferences["batch_provider"] == "colab":
            preferences["batch_provider"] = "local"
        if os.name == "nt" and preferences["live_provider"] == "colab":
            preferences["live_provider"] = "local"
        self.batch_provider = StringVar(value=next(
            label for label, key in self.batch_provider_labels.items() if key == preferences["batch_provider"]
        ))
        self.live_provider = StringVar(value=next(
            label for label, key in self.live_provider_labels.items() if key == preferences["live_provider"]
        ))
        self.colab_session = StringVar(value=preferences["colab_session"])
        self.remote_project = StringVar(value=preferences["remote_project"])
        self.compute_status = StringVar(value="")
        self.log_queue = queue.Queue()
        self.process = None
        self.live_browser_process = None
        self._live_browser_last_message = None
        self._live_browser_error_shown = False

        self._configure_style()
        self._build()
        self._refresh_model_status()
        self.root.after(100, self._drain_log_queue)

    def _configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.configure(bg="#eef2f6")

        style.configure(".", font=("Helvetica", 12), background="#eef2f6", foreground="#17202a")
        style.configure("Shell.TFrame", background="#eef2f6")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#5d6d7e", font=("Helvetica", 11))
        style.configure("Title.TLabel", background="#eef2f6", foreground="#101820", font=("Helvetica", 24, "bold"))
        style.configure("Subtitle.TLabel", background="#eef2f6", foreground="#52616f", font=("Helvetica", 12))
        style.configure("Section.TLabel", background="#ffffff", foreground="#101820", font=("Helvetica", 15, "bold"))
        style.configure("Body.TLabel", background="#ffffff", foreground="#17202a", font=("Helvetica", 12))
        style.configure("Status.TLabel", background="#ffffff", foreground="#1f618d", font=("Helvetica", 12, "bold"))
        style.configure("TButton", padding=(14, 9), font=("Helvetica", 12))
        style.configure("Primary.TButton", background="#1f6feb", foreground="#ffffff", padding=(16, 10), font=("Helvetica", 12, "bold"))
        style.map("Primary.TButton", background=[("active", "#185abc"), ("disabled", "#a8c4ee")])
        style.configure("Danger.TButton", background="#b42318", foreground="#ffffff", padding=(14, 9), font=("Helvetica", 12, "bold"))
        style.map("Danger.TButton", background=[("active", "#8f1d13"), ("disabled", "#ddb0aa")])
        style.configure("TCombobox", padding=(8, 7))
        style.configure("Horizontal.TProgressbar", troughcolor="#d9e2ec", background="#1f6feb")

    def _build(self):
        shell = ttk.Frame(self.root, style="Shell.TFrame", padding=24)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="Video Dubbing Translator", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text="Choose a video, select languages, and generate a dubbed output without touching the terminal.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 18))

        content = ttk.Frame(shell, style="Shell.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=0, minsize=380)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="Panel.TFrame", padding=22)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        right = ttk.Frame(content, style="Panel.TFrame", padding=22)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_controls(left)
        self._build_console(right)

    def _build_controls(self, parent):
        ttk.Label(parent, text="1. Input", style="Section.TLabel").pack(anchor="w")
        ttk.Button(parent, text="Choose Video", command=self.choose_video, style="Primary.TButton").pack(fill="x", pady=(10, 8))
        ttk.Label(parent, textvariable=self.video_path, style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(0, 18))

        ttk.Label(parent, text="2. Languages", style="Section.TLabel").pack(anchor="w", pady=(6, 8))
        ttk.Label(parent, text="Source", style="Body.TLabel").pack(anchor="w")
        self.source_combo = ttk.Combobox(parent, textvariable=self.source_language, values=list(SOURCE_LANGUAGES.keys()), state="readonly")
        self.source_combo.pack(fill="x", pady=(4, 10))

        ttk.Label(parent, text="Target", style="Body.TLabel").pack(anchor="w")
        target_languages = list(TARGET_LANGUAGES.keys())
        self.target_combo = ttk.Combobox(parent, textvariable=self.target_language, values=target_languages, state="readonly")
        self.target_combo.pack(fill="x", pady=(4, 18))

        ttk.Label(parent, text="3. Compute location", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(parent, text="Offline dubbing", style="Body.TLabel").pack(anchor="w")
        self.batch_provider_combo = ttk.Combobox(
            parent, textvariable=self.batch_provider,
            values=tuple(self.batch_provider_labels), state="readonly",
        )
        self.batch_provider_combo.pack(fill="x", pady=(4, 8))
        self.batch_provider_combo.bind("<<ComboboxSelected>>", self._update_compute_status)
        ttk.Label(parent, text="Live Dubbing", style="Body.TLabel").pack(anchor="w")
        self.live_provider_combo = ttk.Combobox(
            parent, textvariable=self.live_provider,
            values=tuple(self.live_provider_labels), state="readonly",
        )
        self.live_provider_combo.pack(fill="x", pady=(4, 8))
        self.live_provider_combo.bind("<<ComboboxSelected>>", self._update_compute_status)
        ttk.Label(parent, text="Colab CLI session", style="Body.TLabel").pack(anchor="w")
        ttk.Entry(parent, textvariable=self.colab_session).pack(fill="x", pady=(4, 8))
        ttk.Label(parent, text="Colab project folder", style="Body.TLabel").pack(anchor="w")
        ttk.Entry(parent, textvariable=self.remote_project).pack(fill="x", pady=(4, 8))
        ttk.Label(parent, textvariable=self.compute_status, style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(0, 12))
        ttk.Button(parent, text="Save compute choices", command=self.save_compute_preferences).pack(fill="x", pady=(0, 14))
        self._update_compute_status()

        ttk.Label(parent, text="4. Required Models", style="Section.TLabel").pack(anchor="w", pady=(6, 8))
        ttk.Label(parent, textvariable=self.model_status, style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(0, 8))
        self.download_button = ttk.Button(parent, text="Download Models", command=self.download_model)
        self.download_button.pack(fill="x", pady=(0, 18))

        ttk.Label(parent, text="5. Run", style="Section.TLabel").pack(anchor="w", pady=(6, 8))
        self.start_button = ttk.Button(parent, text="Start Dubbing", command=self.start, style="Primary.TButton")
        self.start_button.pack(fill="x", pady=(0, 8))
        self.cancel_button = ttk.Button(parent, text="Cancel", command=self.cancel, style="Danger.TButton", state="disabled")
        self.cancel_button.pack(fill="x")
        ttk.Button(
            parent, text="Open Live Dubbing Browser", command=self.open_live_browser,
        ).pack(fill="x", pady=(8, 0))

        ttk.Separator(parent).pack(fill="x", pady=20)
        ttk.Label(parent, text="Output folder", style="Body.TLabel").pack(anchor="w")
        ttk.Label(parent, textvariable=self.output_status, style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(4, 8))
        ttk.Button(parent, text="Open Output Folder", command=self.open_output_folder).pack(fill="x")

    def _build_console(self, parent):
        header = ttk.Frame(parent, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Process Log", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status, style="Status.TLabel").grid(row=0, column=1, sticky="e")

        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(14, 12))

        self.log_text = scrolledtext.ScrolledText(
            parent,
            borderwidth=0,
            relief="flat",
            bg="#101820",
            fg="#e8eef5",
            insertbackground="#e8eef5",
            font=("Menlo", 12),
            wrap="word",
        )
        self.log_text.grid(row=2, column=0, sticky="nsew")
        self._append_log("Ready. Download the model once, then choose a video and start dubbing.\n")

    def choose_video(self):
        filename = filedialog.askopenfilename(
            title="Choose video",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"),
                ("All files", "*.*"),
            ],
        )
        if filename:
            self.video_path.set(filename)
            self._append_log(f"Selected video: {filename}\n")

    def download_model(self):
        if self.process is not None:
            messagebox.showinfo("Busy", "Another process is already running.")
            return
        self._run(
            [sys.executable, str(BASE_DIR / "scripts" / "setup_models.py")],
            "Downloading models",
        )

    def start(self):
        if self.process is not None:
            messagebox.showinfo("Busy", "Another process is already running.")
            return
        if not self.video_path.get():
            messagebox.showerror("Missing video", "Please choose a video file first.")
            return
        try:
            self.save_compute_preferences(show_message=False)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Compute location", str(exc))
            return
        provider = self._batch_provider_key()
        if provider == "colab" and not self.colab_session.get().strip():
            messagebox.showerror("Compute location", "Enter the active Colab CLI session name first.")
            return
        if provider not in ("colab", "modal") and not MODEL_PATH.exists():
            messagebox.showerror("Missing model", "Download the required models before starting.")
            return
        voxcpm_ready, _ = model_ready() if provider == "local" else (True, "Modal selected")
        if not voxcpm_ready:
            messagebox.showerror("Missing model", "Download the VoxCPM2 model before starting.")
            return

        source_code = SOURCE_LANGUAGES[self.source_language.get()]
        target_code = TARGET_LANGUAGES[self.target_language.get()]
        source_arg = "auto" if source_code == "automatic" else source_code

        if provider == "modal":
            command = [sys.executable, str(BASE_DIR / "scripts/run_dubbing.py"), self.video_path.get(),
                       "--compute", "modal", "--source-language", source_arg, "--target-language", target_code]
        elif provider == "colab":
            options = {
                "source_language": source_arg,
                "target_language": target_code,
                "translation_model": "hymt2-1.8b",
                "translation_device": "auto",
                "diarization": True,
                "lip_sync": False,
            }
            command = [
                sys.executable, str(BASE_DIR / "scripts" / "run_colab_batch.py"),
                "--session", self.colab_session.get().strip(),
                "--remote-project", self.remote_project.get().strip(),
                "--input", self.video_path.get(),
                "--output", str(OUTPUT_DIR / Path(self.video_path.get()).name),
                "--options-json", json.dumps(options, ensure_ascii=False, separators=(",", ":")),
            ]
        else:
            command = [
                sys.executable,
                str(BASE_DIR / "Video_Translator.py"),
                self.video_path.get(),
                "--source-language",
                source_arg,
                "--target-language",
                target_code,
                "--tts-backend",
                "modal" if provider == "modal-tts" else "native",
            ]
        self._run(command, "Running dubbing pipeline")

    def cancel(self):
        if self.process is not None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self._append_log("\nCancel requested.\n")

    def open_output_folder(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", str(OUTPUT_DIR)], check=False)
        elif os.name == "nt":
            os.startfile(OUTPUT_DIR)
        else:
            subprocess.run(["xdg-open", str(OUTPUT_DIR)], check=False)

    def open_live_browser(self):
        if self.live_browser_process is not None and self.live_browser_process.poll() is None:
            messagebox.showinfo("Live Dubbing Browser", "The Live Dubbing Browser is already starting or open.")
            return
        log_path = BASE_DIR / "temp" / "live-dubbing-browser-launch.log"
        log_path.parent.mkdir(exist_ok=True)
        LIVE_BROWSER_STATUS.write_text(
            '{"state":"starting","message":"Preparing the separate Live Dubbing browser."}',
            encoding="utf-8",
        )
        try:
            self.save_compute_preferences(show_message=False)
            with log_path.open("a") as log:
                environment = os.environ.copy()
                environment["LIVE_BROWSER_STATUS_PATH"] = str(LIVE_BROWSER_STATUS)
                environment["LIVE_COMPUTE_PROVIDER"] = self._live_provider_key()
                environment["COLAB_SESSION_NAME"] = self.colab_session.get().strip()
                environment["COLAB_REMOTE_PROJECT"] = self.remote_project.get().strip()
                self.live_browser_process = subprocess.Popen(
                    [sys.executable, str(BASE_DIR / "scripts" / "open_live_dubbing_browser.py")],
                    cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=(os.name != "nt"), env=environment,
                )
        except OSError as exc:
            messagebox.showerror("Live Dubbing Browser", f"Could not start the browser launcher: {exc}")
            return
        self._live_browser_last_message = None
        self._live_browser_error_shown = False
        self._poll_live_browser_status()

    def _update_compute_status(self, _event=None):
        messages = []
        if self._batch_provider_key() == "modal-tts":
            messages.append(
                "Modal runs VoxCPM2 speech only; after the job, the app requests Modal's "
                "shortest idle window for GPU containers. Actual shutdown can take longer. "
                "The deployed app and model Volume stay ready for later use."
            )
        if self._batch_provider_key() == "colab" or self._live_provider_key() == "colab":
            messages.append(
                "Colab needs an active CLI session with this project and its models prepared. "
                "The runtime stays open after the job/browser closes. Use Disconnect GPU "
                "in the web GUI to release it. Colab VM files are temporary."
            )
        if not messages and os.name == "nt":
            text = "Colab compute is unavailable here because the upstream Colab CLI does not support Windows."
        elif not messages:
            text = "Processing uses this computer's available CPU/GPU."
        else:
            text = " ".join(messages)
        self.compute_status.set(text)

    def save_compute_preferences(self, show_message=True):
        saved = save_compute_settings({
            "batch_provider": self._batch_provider_key(),
            "live_provider": self._live_provider_key(),
            "colab_session": self.colab_session.get().strip(),
            "remote_project": self.remote_project.get().strip(),
        })
        self._append_log("Saved compute location choices.\n")
        if show_message:
            messagebox.showinfo("Compute location", "Compute choices saved.")
        return saved

    def _batch_provider_key(self):
        return self.batch_provider_labels[self.batch_provider.get()]

    def _live_provider_key(self):
        return self.live_provider_labels[self.live_provider.get()]

    def _poll_live_browser_status(self):
        process = self.live_browser_process
        if process is None:
            return
        try:
            status = json.loads(LIVE_BROWSER_STATUS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {"state": "starting", "message": "Preparing the Live Dubbing browser."}
        message = status.get("message", "")
        if message and message != self._live_browser_last_message:
            self._append_log(message + "\n")
            self._live_browser_last_message = message
        if status.get("state") == "error" and not self._live_browser_error_shown:
            self._live_browser_error_shown = True
            messagebox.showerror(
                "Live Dubbing Browser",
                message + "\n\nThe offline dubbing workflow is still available. "
                "See temp/live-dubbing-browser-launch.log for details.",
            )
        if process.poll() is None:
            self.root.after(750, self._poll_live_browser_status)
        else:
            self.live_browser_process = None

    def _run(self, command, status):
        self.status.set(status)
        self.log_text.delete("1.0", END)
        self._append_log("$ " + " ".join(str(part) for part in command) + "\n\n")
        self._set_running(True)
        thread = threading.Thread(target=self._worker, args=(command,), daemon=True)
        thread.start()

    def _worker(self, command):
        try:
            self.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=(os.name != "nt"),
            )
            for line in self.process.stdout:
                self.log_queue.put(line)
            return_code = self.process.wait()
            if return_code == 0:
                self.log_queue.put("\nDone.\n")
                self.log_queue.put(("STATUS", "Done"))
            else:
                self.log_queue.put(f"\nProcess failed with exit code {return_code}.\n")
                self.log_queue.put(("STATUS", "Failed"))
        except Exception as exc:
            self.log_queue.put(f"\n{type(exc).__name__}: {exc}\n")
            self.log_queue.put(("STATUS", "Failed"))
        finally:
            self.process = None
            self.log_queue.put(("RUNNING", False))
            self.log_queue.put(("REFRESH_MODEL", None))

    def _drain_log_queue(self):
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(item, tuple) and item[0] == "STATUS":
                self.status.set(item[1])
            elif isinstance(item, tuple) and item[0] == "RUNNING":
                self._set_running(item[1])
            elif isinstance(item, tuple) and item[0] == "REFRESH_MODEL":
                self._refresh_model_status()
            else:
                self._append_log(item)

        self.root.after(100, self._drain_log_queue)

    def _append_log(self, text):
        self.log_text.insert(END, text)
        self.log_text.see(END)

    def _set_running(self, is_running):
        state = "disabled" if is_running else "normal"
        self.start_button.configure(state=state)
        self.download_button.configure(state=state)
        self.cancel_button.configure(state="normal" if is_running else "disabled")
        if is_running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _refresh_model_status(self):
        if self._batch_provider_key() == "colab":
            self.model_status.set("Colab supplies the models for this job; local model files are not checked or downloaded.")
            return
        if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
            size_mb = MODEL_PATH.stat().st_size / (1024 * 1024)
            voxcpm_ready, _ = model_ready()
            voxcpm_status = "ready" if voxcpm_ready else "not installed"
            self.model_status.set(f"Vocal model: {size_mb:.1f} MB; VoxCPM2: {voxcpm_status}.")
        else:
            self.model_status.set("Required models are not installed yet.")


def main():
    root = Tk()
    root.lift()
    root.focus_force()
    root.attributes("-topmost", True)
    root.after(1000, lambda: root.attributes("-topmost", False))
    VideoTranslatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
