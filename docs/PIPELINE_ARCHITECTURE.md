# Offline pipeline architecture

This document describes the default batch workflow. Live Dubbing runs through a separate experimental browser-extension/server path and is not part of the offline product's inference chain.

## Compute and UI wrappers

The following inference chain runs on the selected compute host. The web GUI
and `scripts/run_dubbing.py` route full offline work to Local, Colab CLI/G4 or
the private full-pipeline Modal app; Colab/Modal transfer the selected video and
return a local MP4. `compute_backends/` and `modal_batch_app.py` own connection,
preparation and transfer, without replacing the stable inference chain. The
legacy Modal TTS-only adapter remains a separate route. See
[provider details](COMPUTE_PROVIDERS.md) for lifetime and recovery limits.

The main localhost panel has independent Live/Offline selections, advanced
settings disclosure, output/logs and activity feedback. Live supports Local or
Colab; Modal Live is unavailable. The latest UI work changes presentation and
request/status feedback, not the ASR, translation, synthesis or timing algorithms.

## Data flow

```text
input video
  → FFmpeg audio extraction
  → vocal/background separation
  → Whisper transcription + WhisperX word alignment
  → optional local Community-1 diarization
  → transcript/speaker reconciliation and overlap flags
  → safe same-speaker timing groups + duration budgets
  → deterministic speaker-reference selection
  → local Hy-MT2 translation candidate(s)
  → VoxCPM2 voice-conditioned synthesis per group
  → audio/timing/speaker QA, with at most one retry on hard failure
  → bounded timing correction
  → original background + dubbed dialogue reconstruction
  → final video and JSON/CSV/HTML quality reports
```

## Implementation map

| Concern | Main implementation | Behavior |
| --- | --- | --- |
| Job orchestration and CLI | `Video_Translator.py`, `scripts/run_pipeline.py` | The CLI validates inputs and options, then invokes the existing video pipeline. It does not start a hosted service. |
| Separation and media assembly | `vocal-remover/`, FFmpeg helpers in `Video_Translator.py` | Audio stems and final video are created on the selected inference host; cloud wrappers return the final MP4 locally. FFmpeg is supplied by the host/runtime, not bundled as application source. |
| Transcript and alignment | Whisper and WhisperX calls in `Video_Translator.py` | Source words and times provide the basis for speaker reconciliation and timing groups. Models must be present locally for offline operation. |
| Diarization | `diarization/client.py`, `diarization/worker.py`, `diarization/model_manager.py` | A separate environment isolates pyannote dependencies. Community-1 returns ordinary and exclusive diarization; the pipeline uses exclusive turns to choose a dominant speaker for each transcript span and preserves overlap flags. |
| Speaker assignment | `diarization/assignment.py` | Stable IDs are normalized by first appearance. Ambiguous or overlapping spans are flagged instead of borrowing another speaker's identity. |
| Timing groups and references | `dubbing/` and the reference managers in `Video_Translator.py` | Only safe same-speaker spans are merged. A source segment manifest preserves ownership. The chosen reference must be clean and long enough; its exact transcript accompanies VoxCPM2 conditioning. |
| Translation | `translation/hymt2.py`, `translation/hymt2_worker.py`, `translation/model_manager.py`, `translation/base.py` | The main environment keeps its pinned Transformers 4.51.3 stack. Hy-MT2 runs in a separate worker/runtime with Transformers 5.x, reads the selected model from disk, and stays warm for one job. |
| Duration refinement | `dubbing/speech_length.py`, `dubbing/candidates.py`, `dubbing/duration.py` | Spoken-length estimates and per-speaker rate calibration guide candidate sizing. One bounded local Hy-MT2 rewrite may shorten or expand a translation; protected terms and the supplied source text stay in the prompt. |
| Speech synthesis | `voxcpm_runtime.py` and the VoxCPM2 adapter called by `Video_Translator.py` | The pinned VoxCPM2 snapshot is resolved with local-files-only behavior. Each group receives its selected reference WAV and exact reference transcript. |
| Quality reports | `engine/qa.py`, `engine/reports.py` | Local signal/timing/speaker-similarity checks run on generated WAVs. When an ASR evaluator supplies recovered text, WER/CER are also measured. A hard failure can trigger one retry; warnings remain visible. |
| Browser GUI | `web_gui.py`, `start_gui.sh`, `start_gui.bat` | The GUI binds to localhost, reports model readiness, and invokes the same CLI pipeline. It does not download a model just because the page is opened. |

## Offline and network boundary

With Local, network access is used for package installation and explicit model downloads. Selecting Colab/Modal also transfers the selected job media/results through the authenticated provider connection. Community-1 requires the user to accept its model-card conditions before its initial download. VoxCPM2, Hy-MT2, Whisper/WhisperX, and selected alignment assets are loaded from local cache or configured local paths during inference. The normal translation route does not call Google Translate or another translation API.

The code does not bundle model weights, a Python distribution, or FFmpeg. A machine is offline-ready only after its chosen model weights and any language-specific alignment assets are available locally. `scripts/check_setup.py` reports missing assets without downloading them.

## Known behavior and limits

- If Community-1 is unavailable or disabled, the pipeline can fall back to segment references. That fallback does not claim stable multi-speaker identity; install and enable Community-1 for the speaker-aware workflow.
- Community-1 diarization assigns speakers but does not separate concurrent voices. Overlap is flagged; secondary simultaneous speech can remain unresolved.
- Duration refinement balances translated speech length against the original active-speech interval, with safe trailing silence only as additional capacity. Timing can remain limited at configured tempo bounds.
- ASR recovery WER/CER is not a source-to-target translation score. The report keeps those measures distinct.
- The local HTTP GUI has no authentication or multi-user isolation; keep it bound to localhost.
- Live Dubbing remains experimental and is described separately in `docs/LIVE_DUBBING_SETUP.md`.
