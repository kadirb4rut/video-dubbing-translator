# Web GUI compute integration verification — 2026-11-01

## Current behavior

The web GUI exposes Local, Colab CLI/G4 and full-pipeline Modal. Account prompts,
isolated cloud client installation, application upload, remote environment/model
preparation and video transfer are handled from the GUI. Session names and remote
paths are advanced settings. The original inference/timing/QA algorithms were not
edited. The legacy Modal TTS-only route remains separate.

The user's latest policy supersedes the earlier automatic-stop requirement:
**only Disconnect GPU requests cloud shutdown.** Job completion, error, cancel,
Live browser/tunnel closure and GUI closure retain the connected VM/app. Colab
batch cleanup is disabled by default, SSH proxy `--rm` was removed, and GUI
shutdown/heartbeat cleanup was removed. Modal uses an authenticated private
deployment that retains one GPU container until disconnected. Provider runtime
limits and outages remain outside this application's control.

## Actual Colab/G4 full-video test

- User-authorized NASA/JWST English WebM, 228.744 seconds, approximately 29.7 MiB.
- Started through the real web GUI on macOS using the authenticated Colab CLI.
- Dedicated Linux G4 reported NVIDIA RTX PRO 6000 Blackwell Server Edition.
- A new isolated Python 3.12.3 cloud environment was prepared without replacing
  the local AI environment. Hy-MT2 1.8B and the existing pinned VoxCPM2 model were
  used. Community-1 was disabled for this one-speaker test.
- Remote source conversion to an MP4 working copy, separation, Whisper ASR,
  alignment, translation, synthesis, QA, reconstruction and local return ran.
- Output: 228.979 seconds, 1920×1080 H.264, 48 kHz stereo AAC, 57,090,889 bytes.
- Full `ffmpeg` decode passed. HTTP prefix, end/suffix range access and complete
  download passed. The browser Download video action saved a complete MP4.
- Browser download and local output SHA-256 matched:
  `3c58af8663a2b44e04aaeeaf2eedcc6f2018c8af5ccb0b73f98acbdf95a7cc4b`.
- A 12-second output sample was transcribed with the already-cached local
  Whisper base model and recovered Turkish speech. This is a short speech check,
  not a human source-to-target semantic accuracy assessment.
- Existing QA report: 29 groups passed, zero hard failures, 15 warnings. WER/CER
  were not measured in that report. Speaker similarity uses the existing proxy.

This NASA run completed under the *former* auto-stop policy and verified VM
termination then. The user subsequently requested explicit disconnection only.
The new retain-VM policy was covered by success/failure regression checks; the
whole NASA job was not repeated merely to recheck the lifecycle condition.
Drive mounting was not verified in this pass; temporary caching was explicitly
selected. Cached files were reused across setup retries instead of downloaded
again. Drive failure is reported rather than silently falling back.

## Actual Modal full-pipeline test and explicit disconnect

- GUI prepared a private `video-dubbing-full-pipeline` deployment and reusable
  model Volume. Its CUDA runtime/GPU probe passed before GUI status became ready.
- A short synthetic English speech video was submitted through the same new
  `run_dubbing.py --compute modal` adapter that the GUI invokes. This contained
  generated speech rather than uploading the user's NASA file to another service.
- Full remote source processing, translation, VoxCPM2 synthesis and video return
  completed. Returned MP4: 7.084 seconds, 640×360 H.264 and 48 kHz AAC; full decode
  passed.
- After completion: app still `deployed`, one container, function stats
  `num_total_runners=1`, `num_running_inputs=0`, `backlog=0`. Thus completing the
  job did not stop the connected GPU.
- The real GUI **Disconnect GPU** action then changed the app to `stopped`, tasks
  to zero, and the account's container list to empty. The local NASA output
  remained available throughout. The model Volume was preserved.
- The app requests A10G/L4 hardware; this test does not certify all Modal hardware
  types, account limits or larger model selections.

## GUI and regression checks

- Saved full-suite run before the final UI redesign: **324 passed, 15 skipped, 66 subtests passed in 10.63 s**. Skips
  require the explicitly installed optional English syntax pipeline. No missing
  optional model was downloaded to turn a skip into a pass.
- Python compilation and generated JavaScript syntax check passed.
- Account-control requests reject missing request tokens and cross-origin calls.
- A real PTY test verified `/dev/tty` authorization responses without echoing
  the supplied code into logs. Auth URLs remain in memory and are redacted in logs.
- Source bundle tests reject path traversal/symlink entries and exclude personal
  media, models, credentials and arbitrary root-level scripts.
- On a separate GUI test server, real multipart NASA upload passed with the
  request token. Selecting a second file and pressing Start automatically uploaded
  the new file; the expected missing-local-model preflight then stopped execution.
  No GPU was allocated by that negative Local check. Browser console was clean.
- A connected-cloud UI fixture retained Disconnect GPU visibility after selecting
  Local. Selecting Local did not implicitly disconnect the cloud connection.
- Browser screenshot confirmed the restored NASA preview and direct Download
  video link in the user's narrow in-app view. No framework overlay or blank
  page was present. Earlier server-restart fetch errors were development events.
- Last completed output is restored after GUI restart. Streamed playback still
  needs its local GUI HTTP server running; the downloaded MP4 needs neither GUI
  nor GPU. The reported freeze was caused by stopping the GUI development server,
  not by a truncated video artifact.

## Evidence and limits

Detailed local QA JSON/HTML/CSV, source/output hashes, output ASR sample, regression
log and Modal before/after observations are preserved under
`~/Downloads/VideoDubbing_Test_Kanitlari_20261101/`. The user-facing result is
`~/Downloads/NASA_JWST_Turkce.mp4`. No private media or account credentials are
included in this source documentation.

Verified hosts are the current macOS client and the tested Linux cloud runtimes.
Windows Colab is disabled, and clean Windows/Linux consumer installations, local
Mac GPU inference, persistent Drive mounting and larger Hy-MT2 models were not
certified. The later bounded managed Colab Live setup/capture test is documented
in [Live cloud verification](LIVE_CLOUD_VERIFICATION_20261101.md); it supersedes
this offline test's earlier lack of automated-Live coverage. Modal Live remains
unimplemented. The stable Live algorithms were not rewritten.

The subsequent UI redesign/density work has a separate
[verification record](UI_UX_VERIFICATION_20261101.md): 7 targeted tests passed,
desktop/narrow browser checks passed, and the final density edits left backend
and page JavaScript unchanged. No new full-suite or real GPU/audio E2E is implied
by those UI checks. The older full-suite totals above remain dated evidence.
The current GUI/Colab server-restart recovery gap is recorded in
[project readiness](../RELEASE_READINESS.md).

No Git commit, push, PR or GitHub Release was created. These are local changes.
