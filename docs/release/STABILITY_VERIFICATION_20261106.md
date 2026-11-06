# VoxaDub stability hardening and G4 validation

**Tested revision:** `392dda239e15352f40197391ac8f45665881398b`
**Test date:** 2026-11-06
**Scope:** Independent Linux/CUDA findings A1–A6, B1–B3, C1–C2. This is a focused reliability change; it does not alter model weights, public demo media, the v0.1.0 tag, or release history.

## Defect resolution

| Finding | Implemented change and regression evidence | Status |
|---|---|---|
| A1 — transcript timestamps exceed media duration | Normalize Whisper segments against probed media duration; discard intervals outside the media and reject empty or inverted intervals. Boundary tests include an end time of 15.94 s for a 15 s input. | **FIXED AND UNIT-TESTED ONLY** — full CUDA runs used valid inputs; the synthetic matrix did not force a Whisper overrun. |
| A2 — very short speaker references crash or select a wrong voice | Select a clean reference of at least 1 s for a stable speaker, cache it per speaker, reject overlap/clipping/silence and ambiguous identities, and reject an unsafe segment fallback. Tests cover micro-segments and ambiguous identities. | **FIXED AND UNIT-TESTED ONLY** — real synthesis ran on G4, but the synthetic matrix does not establish human-perceived identity preservation for micro-segments. |
| A3 — headless output-folder opener crashes | Return a structured unavailable-opener response while retaining macOS, Windows, and desktop Linux openers. Mocked headless tests cover missing/failing `xdg-open`. | **FIXED AND UNIT-TESTED ONLY** |
| A4 — output video retrieval could expose private media | Keep the GUI server loopback-bound; require a same-origin local UI request with the compute token to mint a short-lived HttpOnly, SameSite cookie grant; resolve and contain output paths under the output root on grant and retrieval; support byte ranges and safe download headers. Tests cover missing authorization, symlink escape, range playback, download headers, and large-file streaming. | **FIXED AND UNIT-TESTED ONLY** — security routes were exercised against the local HTTP server, not an externally reachable deployment. |
| A5 — interrupted uploads can be accepted as complete | Stream multipart content into a temporary sibling file, verify parser completion and expected byte count where supplied, fsync, atomically replace only after completion, and clean up on error. Tests simulate an interrupted multipart socket and incomplete file writes. | **FIXED AND UNIT-TESTED ONLY** |
| A6 — stale browser compute token after a server restart | Return machine-readable `stale_ui_session`; the page refreshes once per session token and asks the user to retry. Origin and compute-token checks remain enforced. | **FIXED AND UNIT-TESTED ONLY** — no real browser restart scenario was part of CUDA inference. |
| B1 — video has no audio | FFprobe input validation reports that the video has no audio to dub before model work. T5 returned the expected actionable rejection on G4. | **FIXED AND GPU-VERIFIED** — pre-inference validation was run on the G4 pipeline environment by design. |
| B2 — audio-only input | FFprobe input validation reports that a video file is required before MoviePy or inference. T6 returned the expected rejection on G4. | **FIXED AND GPU-VERIFIED** — pre-inference validation was run on the G4 pipeline environment by design. |
| B3 — sub-second input reaches a missing-audio mux failure | Inputs shorter than 1.0 s are rejected before pipeline stages that need synthesized audio. T9 returned the documented 0.600 s rejection on G4. | **FIXED AND GPU-VERIFIED** — pre-inference validation was run on the G4 pipeline environment by design. |
| C1 — diarization over-splits speakers | Transcript assignment now requires meaningful overlap and a 0.60 dominant-overlap share; weak or fragmented evidence stays ambiguous and cannot silently borrow another speaker's reference. Short labels are surfaced for review and are never blindly merged. On the synthetic T4 run, unconstrained Community-1 still emitted 3 labels for 2 known speakers. Re-running with the existing `--num-speakers 2` constraint emitted 2 labels across all 12 groups, with zero QA hard failures. | **PARTIALLY RESOLVED** — the known-count control works on G4; unconstrained automatic diarization still over-splits this fixture. No generalized label merge is claimed. |
| C2 — generic internal errors | Add stage/input-aware sanitized UI diagnostics and private mode-0600 logs; keep raw subprocess output and traceback details local. G4 B1–B3 messages were actionable. Unit tests verify redaction and private log permissions. | **FIXED AND UNIT-TESTED ONLY** for general worker failures; B1–B3 user-facing messages were additionally G4-verified. |

The cited `voxadub-local-fixes.patch` and `defect_sweep.sh` were not present in the local Desktop or Codex attachments searched. These changes were implemented independently against the report and current source.

## G4 environment and model readiness

The existing `voxadub-stability` Colab G4 session was resumed and left running with its project/model caches intact. A fresh read-only runtime audit confirmed:

- GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition; `nvidia-smi` reports 97,887 MiB and driver 580.82.07.
- Driver CUDA: 13.0. The Colab notebook kernel itself reports Python 3.13.16 / PyTorch 2.11.0+cu130. This is distinct from the application interpreters used for the pipeline.
- Application plus isolated Hy-MT2 and Community-1 interpreters: Python 3.12.3. All three report CUDA available on the same GPU and passed a real CUDA matrix-multiplication smoke check.
- Application runtime: PyTorch 2.8.0+cu128, Transformers 4.51.3, WhisperX 3.8.6, MoviePy 1.0.3, OpenCV 4.12.0.88.
- Hy-MT2 runtime: PyTorch 2.8.0+cu128, Transformers 5.22.1, Accelerate 1.15.0, Safetensors 0.8.0.
- Community-1 runtime: PyTorch 2.8.0+cu128, `pyannote.audio` 4.0.7, `huggingface_hub` 1.33.0.
- FFmpeg/FFprobe 6.1.1.

The initial VoxCPM2 not-ready result was a cache-location mismatch: the pinned snapshot was stored below the task's configured `HF_HOME`, while a probe that omitted `.vdt-runtime.json` checked the default cache and returned not-ready. With the configured runtime environment, the checkpoint is ready at pinned revision `32279effe8c19989596f05d353d1447f51d9e915`; all seven expected files were present at their expected sizes, and the model loaded for every successful GPU run. No weight files were altered. Hy-MT2 1.8B was present at revision `9a341cd1b679d3efd23b46e847b01745a71ed792`; optional 7B and 30B variants were not installed or downloaded.

The first full Community-1 check exposed two missing PLDA files in the transferred snapshot. Matching files already present in a neighboring local cache were reused in the private Colab cache; their SHA-256 values matched the published files. The readiness check now requires both PLDA transforms, avoiding a false-ready state and a delayed pipeline crash. The isolated Community-1 setup also detects a partial venv without pip and repairs it through `uv venv --clear --seed`, fixing the G4 environment issue without a pip bootstrap guess. Community-1 revision: `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`. The cached Vocal Remover baseline was 59,317,925 bytes, SHA-256 `f0bf9cb226e20571aac8aeda9f6d5f70e495c7b9b3457afe4b11cfec3b515fc3`.

Final inference runs used cached model weights; the default Colab kernel's Python 3.13 environment was not used to run VoxaDub. The app and both isolated model runtimes imported completely under Python 3.12.3, and all six valid E2E scenarios completed through the production pipeline. No model weights, credentials, or private Colab paths are included in this repository evidence.

## Nine-scenario CUDA matrix

Fixtures are synthetic, locally generated eSpeak speech plus FFmpeg `testsrc2` video. No third-party recordings were used. T4 has twelve alternating, timestamped turns from two distinct synthetic voices. Source resolutions/rates were 640×360 at 24 fps unless noted; T4 used 30 fps and T8 used 360×640 at 30 fps. The independent tester's original media was unavailable, so transcript, resolution, and encoding details could not be matched beyond the reported scenario duration/type. The generator and manifest are at [`scripts/create_stability_e2e_fixtures.py`](../../scripts/create_stability_e2e_fixtures.py).

| ID | Input | Result | Wall time | Wall / media | Peak observed VRAM | Output duration | Max group sync error |
|---|---:|---|---:|---:|---:|---:|---:|
| T1 | 15.000 s, one speaker | Passed | 49.29 s | 3.286× | 11,445 MiB | 15.083 s | 0.0748 s |
| T2 | 60.000 s, one speaker | Passed | 80.49 s | 1.342× | 11,497 MiB | 60.125 s | 0.1816 s |
| T3 | 2.208 s, one speaker | Passed | 38.21 s | 17.307× | 11,179 MiB | 2.250 s | 0.1130 s |
| T4 | 75.000 s, two speakers | Passed with limitation | 109.67 s | 1.462× | 11,603 MiB | 75.233 s | 0.1280 s |
| T5 | 15 s video, no audio stream | Expected rejection, exit 2 | — | — | — | — | — |
| T6 | Audio-only WAV | Expected rejection, exit 2 | — | — | — | — | — |
| T7 | 75.000 s, speech followed by long digital silence | Passed | 52.31 s | 0.697× | 11,469 MiB | 75.042 s | <0.0001 s |
| T8 | 15.000 s, portrait 360×640 at 30 fps | Passed | 49.30 s | 3.287× | 11,423 MiB | 15.067 s | 0.0748 s |
| T9 | 0.600 s A/V | Expected rejection, exit 2 | — | — | — | — | — |

All six valid outputs contained H.264 video and AAC audio; FFprobe succeeded and a full FFmpeg decode completed. Audio/video stream-duration deltas ranged from 0 to 0.000667 s. There were no timing-QA hard failures. Maximum group synchronization error across the six runs was 0.1816 s. After fixing the direct-entry exception import, `Video_Translator.py` also rejected T5 with the same actionable no-audio message and no traceback. Peak memory is sampled `nvidia-smi memory.used` approximately every 1.5 seconds, includes runtime CUDA context, and can miss a brief peak; it is not a PyTorch allocator maximum. Wall time includes process startup and model initialization. Each fresh pipeline process loaded VoxCPM2 in about 7.4–7.7 s; the isolated Hy-MT2 worker loaded in about 4.1 s. Model weight downloads were not part of these timed runs.

T5, T6, and T9 were rejected before inference with no traceback. The exact messages are in [`stability-evidence/rejections.json`](stability-evidence/rejections.json). Machine-readable per-run details and sanitized timing excerpts are in [`stability-evidence/`](stability-evidence/).

### Speaker and audio-quality limits

The unconstrained T4 diarizer returned `SPEAKER_00`, `SPEAKER_01`, and `SPEAKER_02`; timestamp-overlap majority mapping put `SPEAKER_00` on synthetic speaker A and both later labels on B. This is a timestamp proxy, not an acoustic embedding comparison or human listening result. The additional `--num-speakers 2` CUDA run returned exactly two labels for 12 groups, zero QA hard failures, and a maximum group timing error of 0.2126 s; it took 110.69 s (1.476× input duration) and sampled 11,603 MiB peak VRAM. Its H.264/AAC output passed full decode. The exact-count option is useful when the true speaker count is known, but unconstrained over-splitting remains a release limitation.

All generated outputs had measurable audio. Speech-heavy outputs measured mean levels around −24.3 to −24.6 dBFS and peaks from −2.4 to −3.9 dBFS. T7's mean was −34.6 dBFS because most of its 75 seconds are intentional digital silence; its peak was −4.0 dBFS. The pipeline's automated speaker-similarity fields are retained in JSON as diagnostics only; they are not human ratings. No subjective voice quality, translation accuracy, or perceived identity improvement is claimed.

This result is not an apples-to-apples speed comparison with the independent RTX 4090 report: the hardware, CUDA stack, synthetic inputs, model cache state, and scheduling differ. Passing on this 96 GB GPU does not prove the model set fits or performs acceptably on a 24 GB RTX 4090. No optional model or untested platform is claimed as verified.

## Reproduction and evidence

1. Generate fixtures locally: `python scripts/create_stability_e2e_fixtures.py /tmp/voxadub-stability-fixtures`.
2. Use the established Colab CLI with the existing `voxadub-stability` G4 session. Apply the private `.vdt-runtime.json` environment and keep Hugging Face offline for inference, so a missing cache fails before any download.
3. From the project root, run each valid fixture through the production command, changing only the fixture path:

   ```sh
   .venv/bin/python scripts/run_pipeline.py /path/to/T1_single_speaker_15s.mp4 \
     --source-language en --target-language tr \
     --translation-model hymt2-1.8b --translation-device cuda --diarization-device cuda
   ```

   For T4's constrained diagnostic, append `--num-speakers 2`. T5, T6, and T9 use the same command and are expected to exit 2 during validation.
4. Validate resulting streams and full decode with `ffprobe` and `ffmpeg -v error -i <output.mp4> -f null -`; keep raw logs and output media in private Colab storage.

The report acknowledges the independent VoxCPM community test as the source of the defect baseline. It does not claim endorsement by VoxCPM or the tester. The downloaded evidence contains only sanitized stage/model timing, model file metadata, output probes, and machine-readable results; raw logs and media remain in the private Colab session.

## Regression tests

- Python suite: **365 passed, 15 skipped**, 3 dependency deprecation warnings, in 15.16 s. All 15 skips are the explicitly optional English syntax-pipeline cases in `tests/test_live_dubbing.py`; none were skipped by a broad collection rule.
- Browser extension: **3 test files passed**, 0 failed.
- Python compile, shell syntax checks, and `git diff --check`: passed.
- The local Python test runner uses Python 3.10 dependencies from the adjacent release checkout; real app/isolated-runtime compatibility and inference were separately verified under Python 3.12.3 on CUDA.
- GitHub Actions on the Pull Request is the authoritative repository CI result and is reported in the PR checks.
