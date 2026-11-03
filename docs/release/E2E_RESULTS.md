# Offline E2E evidence and limits

This record separates measured results from claims that were not tested. The detailed audio, transcripts, model outputs, and original video are local development artifacts and are not included here.

## Latest evidence index — 2026-11-01

- [GUI compute E2E](GUI_COMPUTE_E2E_20261101.md): actual complete 228.744 s NASA
  WebM → Turkish via Colab CLI/G4; output 228.979 s and 57,090,889 B; full decode
  and matching local/download hashes. Separate 7.084 s synthetic full-pipeline
  Modal test retained an idle GPU after work, then actual GUI Disconnect stopped
  its app and containers.
- [Managed Colab Live](LIVE_CLOUD_VERIFICATION_20261101.md): bounded authorized
  Sky News capture, Turkish validated speech, Finish/drain, Stop and source audio
  restoration. Temporary voice fallback; no independent lip-sync/semantic score.
- [Web UI checks](UI_UX_VERIFICATION_20261101.md): 7 targeted post-redesign tests,
  browser interaction/feedback checks and final compact desktop/narrow layout.
  The final density pass changed no backend/page JavaScript and did not rerun
  real audio/model jobs.
- The saved broader suite is **324 passed, 15 skipped, 66 subtests passed** before
  the final UI edits. Counts below belong to older checkpoints, not that suite.

The user's latest cloud policy is explicit Disconnect only. The NASA job's old
automatic-stop observation does not describe the current default. See
[current readiness](../RELEASE_READINESS.md) for remaining lifecycle/platform
and GUI-reconnection gaps. The detailed older measurements below are retained.

## Historical speaker-aware Hy-MT2/VoxCPM2 dubbing run

An existing 72.13-second, two-speaker English sample was processed with Hy-MT2 1.8B and native VoxCPM2 on the Colab G4 development GPU. The repaired run reused a previously computed real Community-1 CUDA diarization result and an existing WhisperX-aligned transcript; it therefore validates the translation, speaker-conditioned synthesis, QA, timing, and video reconstruction path, not a fresh source-to-video pass of every stage.

| Measure | Observed result |
| --- | ---: |
| Source / reconstructed video duration | 72.133 s / 72.367 s |
| Stable speaker IDs | 2 |
| Transcript segments / timing groups | 20 / 11 |
| Overlap-marked source segments | 2; overlapping voices are not separated |
| Per-speaker reference records | 2, with reference transcripts; no fallback in the saved manifest |
| Generated speech requests / generated audio | 21 / 134.56 s |
| Warm dubbing/reconstruction wall time | 104.50 s |
| TTS generation wall time | 44.71 s |
| QA groups passed / hard failures | 11 / 0 |
| QA retries / soft-warning groups | 1 / 6 |
| Mean speech-recognition WER / CER | 9.46% / 3.02% |
| Mean reference similarity | 0.986 MFCC-statistics cosine proxy |

The WER/CER numbers compare ASR recovery from generated Turkish audio against the selected Turkish text. They measure speech realization, not whether the Turkish translation preserves the English source meaning. The speaker score is an MFCC-statistics similarity heuristic, not a speaker-verification model or a measured diarization error rate. There is no independent human semantic score for this run.

### Duration-aware rewrite example

For one group, the selected original-active-speech target was 5.162 s. The initial generated candidate measured 7.20 s; the refined candidate measured 4.80 s. Absolute timing error fell from 2.038 s to 0.362 s (82.2% lower). The refined version landed inside the configured ideal tempo band and the QA report recorded WER 0.083 for its speech against the selected Turkish target. That ASR result does not certify source-to-target semantic equivalence.

### Hardware and software captured by the run

- Colab G4 development VM; `NVIDIA RTX PRO 6000 Blackwell Server Edition`.
- Linux `6.6.122+`, Python `3.13.16`, CUDA `13.0`, PyTorch `2.11.0+cu130`.
- VoxCPM `2.0.3`, pyannote.audio `4.0.7`, faster-whisper `1.2.1`, Transformers `5.20.2` in the recorded runtime.
- Hy-MT2 1.8B and VoxCPM2 weights were loaded locally on that VM; no translation API was part of the recorded run.
- GPU VRAM capacity and peak allocated/reserved VRAM were not recorded. This development GPU is not an end-user dependency or compatibility certification.

## Historical full local Mac pipeline run

An earlier local run processed the 72-second fixture through media extraction, vocal separation, transcription/alignment, Community-1 diarization, Hy-MT2 translation, per-speaker VoxCPM2 synthesis, and reconstruction. Its log reports two stable speakers, 20 transcript segments, and a final video artifact. The run took 4,528.09 seconds on CPU/bfloat16 on an Apple M1 MacBook Pro with 8 GB unified memory. Its source revision and a current QA report were not captured with that run, so it is supporting historical evidence, not the current release gate or a performance recommendation.

## Live Dubbing browser launcher on Colab G4 (2026-10-31)

The product launcher was started through the existing Colab G4 session and
visible VNC desktop with a temporary app profile, preserving the regular Chrome
profile, prior launcher profile, and local models. It reached `ready`, opened
the bundled extension automatically, and showed it enabled in
`chrome://extensions` while Developer mode was off. `chrome://version` showed
the isolated `--user-data-dir`, extension loading flags, and no remote-debugging,
automation, or headless flags. The existing local service returned HTTP 200
with the expected application identity.

The full functional capture flow was run in the same directly launched bundled
Chromium/extension mode: an authorized Sky News Live tab was captured; local
models produced validated speech with about 15.97 seconds of video delay and
15.84 seconds buffered; Finish drained to `Finished` with about 0.31 seconds
remaining; Stop released capture and restored unmuted source audio. Community-1
speaker weights were not present, so the configured neutral fallback voice was
used. The integrated launcher was separately smoke-tested after this flow and
reached `ready`; a second full capture cycle through the GUI wrapper was not
run.

The CLI shortcut `start_live_dubbing_browser.sh --check` returned `ready` and
did not load or download AI models. The launcher-specific tests passed 2/2, and
the repository suite passed 302/302 in the existing live runtime (Python 3.13,
9.76 seconds). `accounts.google.com` displayed its regular sign-in form in the
direct-launch browser, then the user completed sign-in interactively in VNC.
The agent did not enter credentials. This confirms only the current account
session; Chrome for Testing branding remains visible.

## Product launcher, Mac-to-Colab tunnel, and real tab capture (2026-10-31)

The product CLI launcher ran on macOS 15.5 Apple Silicon with
`--backend colab --colab-session vdt-live-launcher-20261031`, targeting the
existing G4 project. It opened bundled Chrome for Testing 156 with the
extension already loaded in its separate application profile; the user's
regular Chrome profile was untouched. The launcher used the authenticated
Colab CLI-backed SSH tunnel bound to local loopback and connected to the
existing G4 Live Dubbing service. No AI model files were installed or
downloaded.

On the authorized Sky News Live tab, Start loaded the available G4 models,
tab capture reached `Capturing`, and the panel showed an English source phrase
with its Turkish translation. The panel then showed `Playing validated speech`
with 16.41 seconds effective video delay and 16.17 seconds buffered. Finish
flushed and drained the remaining confirmed speech to `Finished` (about 0.80
seconds remained in the playback buffer). The last unconfirmed source fragment
was explicitly left untranslated. Stop released the capture and the YouTube
tab's sound indicator returned, confirming source audio restoration. The
temporary neutral voice fallback was used because Community-1 speaker weights
were absent. The displayed A/V delay and buffer verify that the delayed player
path was active; no independent lip-sync measurement was made.

Colab allows only one active SSH connection for this runtime, so the local VNC
SSH forward was briefly closed for the product tunnel, then restored after the
launcher closed. The remote G4 runtime and model files were left intact. The
VNC tunnel again listened on `127.0.0.1:16082` and its noVNC page returned HTTP
200. This was a bounded functional test, not a latency or quality benchmark.
macOS local-GPU inference was not part of this run.

## Other historical release checks — October 31 checkpoint

- At this earlier checkpoint, the repository suite passed 302 tests in the then-existing Colab live runtime; newer saved counts are indexed above.
- Python byte-compilation and the Bash installer/launcher syntax checks pass.
- CLI help and local GUI home/status HTTP smoke checks pass.
- The existing Mac environment has VoxCPM2 and Community-1 files but no Hy-MT2 weights, and only 0.9 GB free disk space. The read-only setup check therefore correctly reports that this machine is not ready to translate. No large model was downloaded to the Mac.
- A clean install was not performed; installers were not allowed to replace the preserved environments.

## Known evidence gaps

- No independent source-to-target semantic accuracy score or human-reviewed translation set.
- No measured peak VRAM or multi-GPU/CUDA compatibility matrix beyond the one recorded G4 run.
- Hy-MT2 7B and 30B-A3B were not runtime-tested.
- Windows and clean Linux consumer installations were not tested in this release pass. macOS 15.5 Apple Silicon was tested as the launcher/browser host with Colab G4 as inference; local Mac inference was not tested.
- The sample's redistribution permission is not recorded in the repository. Its before/after video files remain local-only pending rights clearance.
- Google sign-in was completed by the user in the separate G4 VNC browser; the agent did not enter credentials. This does not establish a general sign-in guarantee for Chrome for Testing, other accounts, or networks. The public Sky News stream used for the Mac-to-G4 capture did not require sign-in.
