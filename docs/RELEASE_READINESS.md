# Project status and release readiness

Last reconciled: **2026-11-02**. The original publication target was 2026-10-31;
this remains an **unpublished local candidate**; v0.1.0 is proposed but not tagged or released.
The workspace has no `.git` directory. No commit, push, PR, GitHub Release or
public Discussion was created. Passing a bounded test does not close the
remaining release gates below.

## Current product

`./start_gui.sh` opens one localhost web panel in an isolated Google Chrome profile
for Offline Video Dubbing and Live Dubbing. On macOS it asks before installing
missing Chrome. Each workflow has its own compute-provider choice. The default
local offline pipeline remains Whisper/WhisperX, optional Community-1, Hy-MT2,
VoxCPM2 and FFmpeg. Provider wrappers transfer work to the selected cloud host;
the UI work did not change inference, timing, QA or synchronization algorithms.

| Workflow/provider | Implemented behavior | Verification and limits |
| --- | --- | --- |
| Local offline | Existing full pipeline on the user's available CPU/GPU | Historical full CPU run and bounded speaker-aware G4 evidence exist. The last checked Mac lacks selected Hy-MT2 weights; its current full local GPU workflow and clean installation are not certified. |
| Colab CLI offline | GUI account/setup flow, dedicated G4, full remote pipeline, MP4 return | Real 228.744 s NASA English WebM → Turkish completed through the GUI. Full decode, playback/range access and complete download were checked. The cache is temporary VM storage. |
| Modal offline | Private full-pipeline GPU app, automatic transfer and persistent model Volume | A 7.084 s synthetic English-video test returned a decoded Turkish MP4. The app retained a GPU runner after the job; actual GUI Disconnect stopped the app/containers. This is not a full NASA or broad model/hardware test. |
| Local Live | Google Chrome, automatic extension loading using experimental DevTools, separate profile, local service | The new Chrome 156 launcher loaded the extension and opened its panel on the user's playing stream; local Mac GPU inference and audio capture are not yet verified. |
| Colab CLI Live | Local Chrome/source video; selected tab audio goes through an authenticated loopback-only SSH tunnel to G4 inference | Earlier Sky News capture and Turkish playback used Chrome for Testing. The new Chrome launcher has not yet been validated through live capture and dubbed output. No general latency, semantic-quality or speaker-cloning guarantee. |
| Modal Live | Not implemented; disabled in GUI and rejected by preparation routes | Offline Modal results do not establish Live support. |
| Legacy `modal-tts` | Separate optional VoxCPM2-only route | Kept for existing deployments; its lifecycle and coverage are distinct from the new full-pipeline Modal app. |

## Chrome launcher check — 2 November 2026

On macOS 15.5 Apple Silicon, `./start_gui.sh` launched installed Google Chrome
156 using `ChromeAppProfile`. A separate Live Chrome profile loaded the current
extension through DevTools; the extension action opened its side panel and the
user's YouTube live video played. A GUI preview exercised **Open Live Dubbing
Browser** with the real local service and observed the launcher reach `ready`.
The isolated local preview was then stopped; the existing Colab G4 was left open.
The targeted GUI, Live and pipeline QA suite reported 94 passed, 15 skipped,
and 20 subtests passed. A separate temporary Chrome profile also captured one
real audio track from the user's playing YouTube tab; the measured peak RMS was
0.39, and the track was stopped without sending audio to G4. This exercised the
extension's source/capture messages directly, not the complete Start/Finish UI.
After removal of Drive integration, the real G4 setup and GUI connection reached
ready using the VM cache, and the GUI launched Chrome with its extension and G4
tunnel. A separate service-level test sent 15 seconds of the user-authorized NASA
fixture to that G4: three Turkish phrases and PCM chunks were generated, and
end-of-stream was received. This did not exercise browser playback, A/V sync or
the full extension Start/Finish/Stop sequence. The DevTools loopback port remains open during a
Live browser session and can be used by other processes on the same computer.

## Cloud lifetime and current limitations

The user's latest policy is **explicit disconnection only**. Finishing, failing
or cancelling a job, closing the Live browser/tunnel, or closing the GUI does
not request cloud VM/app termination. **Disconnect GPU** requests release of
managed compute. Allocated idle resources can continue consuming credits;
Modal Volume storage remains separate. Provider quotas,
outages and runtime limits can still end a session independently.

The NASA E2E ran under the former automatic-stop policy and verified termination
then. The later retain-on-completion policy has regression evidence; that full
NASA job was not repeated solely to test the new lifetime rule. Real explicit
Modal disconnection was verified. A separate current-policy real Colab
Disconnect E2E is not established by the saved report.

A GUI restart check also found that a new server process does not automatically
adopt an already-running Colab connection's in-memory state. The panel can show
disconnected, with Disconnect disabled, while an older launcher/tunnel/runtime
is still active. The UI redesign did not fix this recovery limitation. Provider
state must be checked before allocating another runtime; see
[compute-provider details](COMPUTE_PROVIDERS.md#gui-restart-and-connection-state).

## Latest web UI and UX

The main panel now uses a lighter blue workspace, distinct Live/Offline task
areas, output and logs on the right at desktop sizes, and collapsed advanced
model/speaker/lip-sync settings. Buttons show request feedback; a sticky activity
message displays real server states, errors and completion. Indeterminate
animation indicates waiting, not a measured percentage or time remaining. Live
capture/Finish controls remain in the existing extension panel.

The subsequent compact-density pass was inspected in the user's Chrome at
100% scale, 1440×722 CSS pixels. Desktop controls changed from 44 to 36 px,
body text from 15 to 14 px, panel padding from 24 to 16 px, and maximum content
width from 1520 to 1280 px. The Live panel measured about 336 → 272 px high.
A 390 px narrow viewport had no horizontal overflow; coarse-pointer controls
retain 44 px targets. Provider toggling and advanced-setting disclosure worked,
and no new console error was observed in the final polling window. These are
frontend checks, not a new GPU/audio E2E. See the
[UI verification record](release/UI_UX_VERIFICATION_20261101.md).

The redesign was shown on a separate local preview server. An already-running
main GUI process continues serving the code it loaded until restarted; merely
reloading that old process does not load the edited Python page generator.

## Measured evidence and test chronology

| Check | Result | Evidence/scope |
| --- | --- | --- |
| Full saved regression suite, November 1 | **324 passed, 15 skipped, 66 subtests passed**, 10.63 s | Retained `regression.txt`; skips require an optional explicitly installed English syntax pipeline. This run predates the final UI/density edits. |
| Post-redesign targeted tests | **7 passed**, 2.76 s | `tests/test_cloud_gui.py` and `tests/test_live_browser_launcher.py` on the project Python, using the existing pytest installation with unrelated plugin autoload disabled. No full-suite rerun is claimed for the redesigned UI. |
| Compact-density source checks | PASS | Python syntax check; backend AST and page JavaScript matched the pre-density source. Density changes were CSS plus two heading font sizes. |
| Frontend behavior | PASS within tested scope | Desktop/narrow screenshots, Local↔Colab visibility, advanced settings, missing-file error; a temporary mocked job verified busy → Done feedback. Mocked progression is not a real cloud/model run. |
| Colab full offline E2E | PASS, bounded | Source 228.744 s; output 228.979 s, 1920×1080 H.264, 48 kHz stereo AAC, 57,090,889 B. Output/download SHA-256 matched `3c58af8663a2b44e04aaeeaf2eedcc6f2018c8af5ccb0b73f98acbdf95a7cc4b`. QA: 29 groups passed, zero hard failures, 15 warnings. |
| Modal full offline and explicit Disconnect | PASS, bounded | Synthetic source; 7.084 s returned MP4. One idle runner after the job; stopped app and zero containers after the GUI Disconnect action. |
| Managed Colab Live, November 1 | PASS, bounded functional check | Sky News tab capture and Turkish validated speech; roughly 16 s displayed delay/buffer, Finish/drain, Stop and source sound restoration. Community-1 absent; temporary voice fallback. No independent lip-sync/semantic assessment. |
| Earlier speaker-aware offline run | PASS with limits | 72.133 s source / 72.367 s output; reused aligned transcript and Community-1 result; 11/11 QA groups passed. Not a fresh run of every stage. |
| Run30/Run31 | Historical experimental-candidate evidence | 5/15 minute runs, E2E p95 13.666/21.712 s. Not matched A/B; tested scheduler candidate excluded from the stable tree. No universal p95 below 15 s. |
| Platform coverage | Partial | Existing macOS 15.5 Apple Silicon browser/client and recorded Linux G4/Modal runtimes. Windows Colab is disabled. Clean Windows/Linux consumer installs, other GPUs and local Mac GPU inference remain unverified. |

The earlier October 31 suite of 317 tests and 24 targeted provider/CLI/browser/
documentation checks remains historical evidence, not the latest suite count.
Its isolated G4 source snapshot was `/content/.vdt-provider-verify-final-1793423671/`.
No current availability of that ephemeral runtime is implied.

Detailed current provider evidence is in
[GUI compute E2E](release/GUI_COMPUTE_E2E_20261101.md) and
[Live cloud verification](release/LIVE_CLOUD_VERIFICATION_20261101.md).
Historical metrics remain in [E2E results](release/E2E_RESULTS.md) and
[Live status](release/LIVE_DUBBING_STATUS.md). The saved November 1 local evidence
folder contains the manifest, regression log, QA reports, output ASR sample and
Modal before/after observations; its presence was checked during this report
update. No new model job or cloud stop was performed for this documentation audit.

## Remaining release gates

1. Confirm the proposed v0.1.0 version/tag against the real Git repository and obtain the
   user's explicit publication approval. The October 31 target is not proof of
   a published release.
2. Validate a clean installation and one bounded workflow on each platform that
   will be advertised. Command availability alone does not certify Windows/Linux.
3. Close persistent Drive setup and GUI/Colab reconnection-state gaps. Record a
   real explicit Colab Disconnect test under the current retention policy.
4. Keep Modal Live marked unavailable. Keep Live experimental: no universal
   latency, semantic quality, multi-speaker identity or production guarantee.
5. Do not imply full Community-1 speaker cloning from Live tests that used its
   fallback voice. Larger Hy-MT2 variants, new CUDA/GPU combinations, peak VRAM
   and current optional LatentSync behavior need separate evidence.
6. Record source/demo redistribution permission before publishing development
   media. Technical capture permission and private test access are not a public
   redistribution record. See [demo media](release/DEMO_MEDIA.md).

Installation/use: [README](../README.md),
[Turkish GUI Quick Start](GUI_COMPUTE_QUICK_START_TR.md),
[updated Word manual](KULLANIM_KILAVUZU_TR_GUNCEL_20261101.docx),
[providers](COMPUTE_PROVIDERS.md), [Live setup](LIVE_DUBBING_SETUP.md).
Publication text: [local Release draft](release/GITHUB_RELEASE_DRAFT.md).
Hardware limits: [matrix](release/CUDA_TEST_MATRIX.md).
Licensing: [third-party notices](../THIRD_PARTY_NOTICES.md).
