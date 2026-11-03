# Live Dubbing status for this release candidate

Live Dubbing is a separate **experimental** feature. It has real browser/YouTube development runs, but no universal latency, translation-quality, speaker-identity, or livestream-production guarantee. The two-chunk TTS scheduler lease candidate was tested in Run30 and Run31 and is excluded from the stable tree: those runs were not a controlled matched A/B and did not establish a repeatable scheduler benefit. The saved candidate is under the local `temp/live-validation-20261030/` evidence directory.

## Current product checkpoint — 2026-11-01

The bundled-extension launcher and authenticated macOS-to-Colab G4 tunnel have
bounded real Sky News capture, Turkish playback, Finish/drain and source-sound
restoration evidence, including [managed cloud setup](LIVE_CLOUD_VERIFICATION_20261101.md).
Community-1 was absent in those runs, so they do not certify speaker cloning.
The main web panel now offers Local/Colab Live choices, plus a separate Offline
Local/Colab/full-Modal choice. Modal Live remains unavailable. Connected cloud
compute is retained until explicit Disconnect; GUI restart recovery has a known
Colab state gap. See [current project status](../RELEASE_READINESS.md).

The [UI/density checks](UI_UX_VERIFICATION_20261101.md) are frontend evidence and
are not new live audio or long performance measurements. The Run30/31 metrics
below remain historical evidence for their excluded scheduler candidate.

## Historical real YouTube browser runs

Both runs used the authorized YouTube Live page on the Colab G4 development VM. They used the experimental `live_dubbing/models.py` candidate with SHA-256 `ee74c6b845fa5db39ddaf62877576c3159cb9a57ef4d9ab2682579da40256a4c`; this is not the stable working-tree version.

| Measure | Run30 — 5 min | Run31 — 15 min |
| --- | ---: | ---: |
| Requested / actual capture | 300 / 300.362 s | 900 / 905.2 s |
| Committed / unique phrases | 26 / 26 | 166 / 166 |
| Ready / played acknowledgements | 26 / 26 | 166 / 166 |
| Voiced / caption-only | 24 / 2 | 159 / 7 |
| Skipped / duplicate phrases | 0 / 0 | 0 / 0 |
| Speech-end → playback p50 / p95 / max | 9.108 / 13.666 / 13.679 s | 13.544 / 21.712 / 26.081 s |
| Ready-audio FIFO wait p50 / p95 / max | 2.343 / 6.895 / 7.145 s | 6.792 / 14.287 / 18.860 s |
| Video delay p50 / p95 / max | 4.007 / 14.488 / 15.987 s | 15.497 / 24.057 / 28.438 s |
| Live-edge → displayed video p50 / p95 / max | 12.767 / 14.646 / 14.789 s | 16.436 / 24.116 / 28.116 s |
| Absolute A/V offset p50 / p95 / max | 40 / 125 / 168 ms | 46 / 147 / 214 ms |
| Browser queue depth p95 / max | 3 / 3 phrases | 5 / 8 phrases |
| TTS execution p95 / QA p95 | 3.574 / 0.484 s | 4.030 / 0.513 s |
| Finish / drain / final state | pass / 12.016 s / Inactive | pass / 22.031 s / Inactive |
| Original audio restored | yes | yes |

Run30's 13.666-second E2E p95 is a bounded five-minute observation, not a stable or causal improvement. Run31's 21.712-second p95 is materially higher. These captures contain different spans and run lengths, so they are not a matched comparison and must not be presented as a regression or improvement caused by the candidate. The prior Run19 reference was a 30-minute baseline with 23.833-second speech-end-to-playback p95 and 19.90-second ready-audio queue-wait p95. It also is not a matched comparison to Runs30/31. No run supports a universal p95 below 15 seconds.

The scheduler bottleneck is not established by these runs. In Run31, TTS compute p95 was 4.03 seconds and translation p95 0.86 seconds, while ready-audio FIFO wait p95 was 14.287 seconds. This shows substantial presentation queue waiting in the tail; it does not establish that the tested lease scheduler fixes it. Video play failures were zero; the A/V offset p95 stayed below 0.15 seconds. The video delay slope over the full capture was −0.437 seconds/minute, but delay varied widely and peaked at 28.438 seconds, so this single run does not certify steady low latency.

Run31 committed 618.62 seconds of VAD-marked speech out of 654.112 seconds, leaving 35.492 seconds of VAD activity outside committed phrases. This telemetry alone does not prove that the full difference was spoken words or that words were lost. The report does explicitly warn that one final source phrase was not confirmed and remains untranslated. Translation semantics, Turkish naturalness, and speaker identity were not independently rated for Run30/31. Caption-only output is reported separately and is not counted as voiced playback.

Finish/drain did complete in both runs. The Run31 final state was `Inactive`, the server was inactive after stop, and the source reported `captureActive=false`, `ownsMute=false`, and `preCaptureMuted=false`.

## Evidence files

- Run30 report/events/source: `temp/live-validation-20261030/run30-report.json`, `run30-events.jsonl`, `run30-captured-source.wav`.
- Run31 final report: `temp/live-validation-20261030/run31-report.json`.
- Run31 progress and runner log: `temp/live-validation-20261030/run31-progress-latest.jsonl`, `run31-runner-latest.log`.
- Stable `live_dubbing/models.py` has the original SHA-256 `22bf5cdcda24f764c678cf91310be072aa0154c051cfb9a06153bd092c0f62ec`. The experimental file and its test snapshot remain under `temp/live-validation-20261030/` for audit; they are not part of the release surface.

At the Run31 checkpoint, the already-started 15-minute test was allowed to finish and no additional live run was started in that phase. The G4 VM was left intact then. Later short launcher/tunnel tests are recorded above; this historical paragraph is not a statement of present VM allocation. No commit, push, PR, Release or public Discussion was created.
