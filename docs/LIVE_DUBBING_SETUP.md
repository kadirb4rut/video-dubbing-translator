# Optional Live Dubbing: local setup and use

Status: **experimental**, unpublished. Current scope and outstanding release gates are in [project/release readiness](RELEASE_READINESS.md) and [November 1 Live cloud verification](release/LIVE_CLOUD_VERIFICATION_20261101.md); the [engineering record](LIVE_DUBBING_ENGINEERING.md) preserves earlier experiments. Existing Video Dubbing stays the default and does not start capture or live models.

Use the existing project environment and the separate Hy-MT2 and Community-1 runtimes. Install only the optional transport:

```sh
python -m pip install -r requirements-live.txt
python scripts/setup_live_segmentation.py --install-runtime --download
```

Set up the selected Hy-MT2 **1.8B** model and Community-1 through the existing explicit setup/model-manager commands. Accept Community-1 access terms using your own Hugging Face account if required. Install the native VoxCPM2 weights and a cached faster-whisper `small` model explicitly. Live startup reads local caches; it never downloads missing models. Hy-MT2 7B/30B are not needed or validated for Live Dubbing.

The boundary setup explicitly creates an isolated CPU wtpsplit 2.2.2 runtime and downloads about 442 MB of SaT/tokenizer files, plus runtime dependencies. It does not upgrade the main VoxCPM2 environment. Without flags, the setup command only reports status. The shared policy uses no language-specific parser or grammatical word lists. It discards SaT's input-final score, requires actual right context or an acoustic turn, and confirms the source prefix across real ASR observations before TTS. It selects one complete contextual translation; identical translations across repeated calls are not required and would not prove meaning. Recent real tests still found ASR and translation defects; see the [multilingual research record](research/UNIVERSAL_LIVE_DUBBING_20261026.md).

The main live runtime needs the project's native VoxCPM2, faster-whisper, ONNX Runtime, NumPy, SoundFile and SciPy dependencies. Hy-MT2, pyannote and sentence segmentation run in isolated subprocesses. `LIVE_PYTHON` can select a prepared main live environment when using the existing GUI. By default, inference stays on this computer. The GUI can instead select a prepared active Colab runtime; the selected tab's captured audio is forwarded through an authenticated loopback-only SSH tunnel and the AI models stay in that Colab runtime. Colab setup and its platform/policy limits are covered in [Compute providers](COMPUTE_PROVIDERS.md).

For the default local service, start the existing GUI and open **Live Dubbing**, or run:

```sh
python -m live_dubbing.server
```

Open `http://127.0.0.1:8766/`. The status endpoint reports installed models without loading them. If your ASR cache is in a separate directory, set `LIVE_WHISPER_MODEL` to that directory. Other optional local path overrides are `LIVE_VOXCPM_PATH`, `LIVE_SPEAKER_MODEL`, `LIVE_SPEAKER_PYTHON`, `HYMT2_MODELS_DIR` and `HYMT2_PYTHON`. These are filesystem/runtime paths, not cloud credentials.

Boundary runtime overrides are `LIVE_SEGMENTATION_PYTHON`, `LIVE_SEGMENTATION_MODEL` and `LIVE_SEGMENTATION_TOKENIZER`. The former `LIVE_SYNTAX_MODEL` English parser experiment is no longer activated by LiveSession.

Optional native CUDA compilation can be enabled with `LIVE_VOXCPM_COMPILE=1` and an explicit `--device cuda` (or `cuda:N`). It uses VoxCPM's own `torch.compile` path, preserving the model, 10 inference steps, conditioning and audio QA. The default is `0`; setting it back to `0` selects the existing eager native fallback. Offline dubbing is unaffected. Startup waits for model warm-up before offering capture, but new input shapes may still incur compilation. A bounded RTX PRO 6000 / Torch 2.11 / Triton 3.6 probe measured warm TTFA about 27 ms and native RTF 0.206 on short utterances, with exact ASR recovery in seven normal/cloned samples. The first compiled normal request took 39.32 s including compilation. These are component measurements, not end-to-end latency or hardware compatibility guarantees. See the [official native optimization API](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py).

An experimental Qwen3.5-27B adapter is available for Live Dubbing. The default remains Hy-MT2; offline translation is unchanged. This large model was tested on a 96 GB RTX PRO 6000 with about 55 GB peak tensor allocation in its standalone probe, and adds substantial storage/GPU requirements. It can propose phrase boundaries within twice-confirmed ASR, using actual right context; the controller still enforces timestamp, lookahead and source-budget checks. Translation is a separate request for the selected source span. A narrow same-language comparison can distinguish minor differences between intended TTS text and independent ASR, while retaining physical audio checks, original error metrics and the repetition guard. These model judgments and the model-card language count do not certify semantic correctness or quality in every language.

Boundary proposals can be enabled separately from translation with `LIVE_BOUNDARY_BACKEND=qwen3.5-27b`. This keeps `LIVE_TRANSLATION_BACKEND` on its default Hy-MT2 route and uses Qwen only to propose a candidate end within stable, timestamped ASR words. The same proposal must survive a separate controller observation with additional rolling-ASR context; this gives the exact-span Hy-MT2 draft time to finish before ownership transfers. Existing right-context and source-ownership checks still decide whether any phrase may commit. SaT remains active for pause/acoustic evidence. If Qwen is already selected as the translation backend, it remains the default boundary proposer for compatibility; set `LIVE_BOUNDARY_BACKEND=sat` to explicitly disable its boundary role. The optional boundary provider requires the same already-installed local 56 GB Qwen weights and compatible worker runtime; it is never downloaded implicitly. Running it alongside Hy-MT2 increases VRAM pressure and GPU scheduling contention, so it must be measured on the target G4 before any performance claim. Status reports model-file readiness without loading weights; runtime compatibility is checked when the session starts.

Boundary-only configuration on an inference machine that already has the Qwen files/runtime:

```sh
export LIVE_TRANSLATION_BACKEND=hymt2-1.8b
export LIVE_BOUNDARY_BACKEND=qwen3.5-27b
export LIVE_QWEN_MODEL=/your/model/directory
export LIVE_TRANSLATION_PYTHON=/your/prepared/runtime/bin/python
```

To select it explicitly, prepare a separate Python environment with compatible PyTorch and Transformers 5.20+, then use these commands on the inference computer:

```sh
python scripts/setup_live_qwen.py --model-dir /your/model/directory --download
export LIVE_TRANSLATION_BACKEND=qwen3.5-27b
export LIVE_QWEN_MODEL=/your/model/directory
export LIVE_TRANSLATION_PYTHON=/your/prepared/runtime/bin/python
```

Without `--download`, setup only reports file readiness. Model files require about 56 GB and setup preserves an additional 10 GiB reserve; existing complete shards are reused. No packages are installed or upgraded by this command. The worker loads local files only, runs direct non-thinking translation, requires complete JSON plus EOS, and records token limits and actual model revision. Source LocalAgreement, contextual/acoustic boundary confirmation, explicit glossary checks and audio QA remain active. Whole-target string equality is not a playback gate. Missing setup or a failed response stops explicitly; it does not silently switch engines. Stop releases the translation worker together with the other live models.

## Open the Live Dubbing Browser

Run `./start_gui.sh` and choose **Local** or **Colab CLI** in the
Live Dubbing section of the main panel. You can click **Connect & prepare
Colab**, then **Open Live Dubbing Browser**, or choose Open to connect and launch
in one flow. Offline Dubbing has its own provider setting in the same GUI. The
saved live provider choice is also used by `./start_live_dubbing_browser.sh`
(Windows: `start_live_dubbing_browser.bat`); CLI overrides are described in
[Compute providers](COMPUTE_PROVIDERS.md). The launcher reuses or starts the
selected service without changing model files, then opens Google Chrome using
a persistent application-only profile. YouTube sign-in, if needed, is separate
in this profile; your regular Chrome profile and cookies are not copied.

On macOS, if Chrome is missing, the terminal asks before downloading Google's
installer and placing Chrome in the app's data folder. Windows and Linux
currently require Chrome to be installed first. Browser setup never downloads
AI model weights. Run `python scripts/open_live_dubbing_browser.py --check` to
check Chrome and its small browser connection dependency without starting the
Live service. On macOS `--use-mock-keychain` avoids a Keychain prompt but also
means saved credentials in the app profile lack normal Keychain protection.

The extension is loaded automatically from `browser_extension/live_dubbing`;
there is no Chrome Web Store, Developer Mode, or Load unpacked step. The
launcher installs the extension into the isolated Chrome session through
Chrome's experimental DevTools protocol. A random loopback debugging port is
open until that browser closes; other processes on the same computer can use it
to control the isolated browser. Chrome 156 on macOS was checked for extension
loading, action/side-panel opening and YouTube playback. Live capture and
translated playback in this new configuration remain to be verified. Earlier
bounded Colab G4 evidence used Chrome for Testing. Windows and Linux are not
claimed as tested.

## Pair and start a session

1. In the main GUI, choose **Local** or **Colab CLI** under Live Dubbing and click **Open Live Dubbing Browser**. For Colab, setup runs first and uses the selected cloud session. The CLI equivalent is `./start_live_dubbing_browser.sh` (or pass `--backend colab --colab-session my-g4`).
2. In this Chrome window, open the authorized YouTube Live stream or supported video, then click the loaded Live Dubbing extension action in the toolbar (it may be under the Extensions menu). If asked, allow the extension on this YouTube site only. The side panel opens beside that source tab. This click is intentionally kept as the browser's user gesture for tab audio capture; capture never starts automatically.
3. In the main `http://127.0.0.1:8765/` panel, use **Create one-use pairing code**, then **Copy pairing code**. Enter it in the extension side panel. Codes expire after 120 seconds; create a fresh code for each session.
4. Choose source/target language and latency preference, then confirm you are authorized to capture and translate the source.
5. Click **Start Live Dubbing** and wait for **Ready to capture**. This loads the already-installed models on the selected compute; capture has not started. If a required model is missing, use its existing explicit setup action. The launcher does not download it.
6. With the authorized source tab still selected and the side panel visible, click **Choose permitted source & capture**. Keep this tab and panel visible during capture and Finish/drain. Switching away or hiding the panel ends capture to protect timing.
7. **Pause** pauses presentation while capture remains bounded; **Resume** continues it. **Original** plays the captured original track on the same delayed video timeline.
8. Click **Finish capture & play remaining speech** to flush the final PCM packet and drain confirmed speech. Wait for **Finished**, then click **Stop** to release inference workers and restore ordinary source audio immediately.

Live Dubbing on Modal is not currently available. Modal currently handles
offline dubbing; its streaming backend has not been implemented. Choose Local
or Colab CLI for live sessions.

The extension retains only the existing narrow permissions: `activeTab`,
`tabCapture`, `sidePanel`, session `storage`, and access to
`http://127.0.0.1:8766/`. It requests no microphone, cookies, history or broad
browsing permissions. Capture and audio restoration still depend on Chromium
permissions and explicit user actions.

Launcher and YouTube/model E2E verification status is recorded in
[release readiness](RELEASE_READINESS.md). The product launcher was verified
on macOS 15.5 Apple Silicon through the real Colab tunnel to the Ubuntu 24.04.4
x86_64 G4: Sky News Live capture, Turkish validated speech, delayed-player
telemetry, Finish/drain, Stop, and source-audio restoration. Community-1 was
not installed, so this run used the neutral voice fallback and did not verify
speaker cloning. Windows, local Mac inference, and other Linux distributions
were not tested.

Only one live session can be active at a time. A fresh session needs a fresh pairing code. Navigation/reload/closure of the selected source invalidates that selection. Source seeks and playback-rate changes are not controlled through YouTube APIs; capturing the rendered tab keeps a wall-clock timeline, but ASR continuity across abrupt source changes remains experimental. Stop and restart at a clean boundary after a seek.

YouTube support uses only explicit full-tab browser capture. Embedded/API players, extracted stream URLs, protected media workarounds, DRM/ad modifications and automatic page capture are unsupported. Content and platform permissions still apply; the authorization checkbox does not certify legal permission or override restrictions. If the browser blocks media access, do not bypass it. Generic display capture is a separate experimental route and cannot reliably mute the original tab automatically.

Quality safeguards hold each generated utterance until signal/ASR QA completes, with at most one regeneration. Native PCM generation is incremental; playback is deliberately delayed until QA succeeds. If both audio candidates fail QA, that phrase is explicitly shown as text without rejected speech, and subsequent phrases can return to native speech. Text fallbacks are counted separately from synthesized and played speech. Uncertain speaker identity uses a neutral model voice and an explicit notice; it never updates a voice reference or claims separation of simultaneous speech. Only the dominant stable ASR transcript is translated in that case, so overlapping secondary dialogue may remain unrecoverable. Unstable/incomplete source content and overload stop with a warning. Automatic source-language detection remains automatic across windows; a changed language invalidates only uncommitted ASR agreement. This is not guaranteed 1–2 second latency. Input queues, phrase queues, model conditioning caches and encoded video buffers have hard limits; overload stops instead of silently skipping speech.

Explicit terminology is a literal source-to-target mapping. The same Unicode check applies to every language. If a present source term loses its mapped target spelling, translation stops before speech; the backend never appends the missing term as unrelated audio. This check does not certify translation meaning, and localized names/units may require a suitable user-chosen mapping.

Diagnostic text, candidate audio, immutable speaker references and timing records are written under `temp/live-dubbing/<session>/` on the selected inference host. They can contain captured speech/transcripts. With Local this is the local machine; choosing Colab sends the permitted tab audio to that selected cloud runtime. These records are not published automatically or sent to a third-party translation API. Retain or remove these local diagnostics according to your privacy needs. Avoid packaging this directory indiscriminately or including browser profiles, authentication files or model weights.
