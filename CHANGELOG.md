# Changelog

## v0.1.0 — proposed, unpublished

### Added

- Experimental Live Dubbing for authorized YouTube Live and supported VOD, with explicit extension pairing and capture, adaptive buffering, and Local or Colab CLI processing.
- Automatic Chrome launcher and extension loading in a separate profile; no Web Store or manual extension installation required.
- Local Hy-MT2 translation, contextual terminology and duration-aware refinement; explicit model management and isolated runtimes.
- Optional Community-1 diarization, stable speaker IDs and speaker-specific VoxCPM2 references with matching reference transcripts.
- GUI choices for Local, Colab CLI G4 and full-pipeline Modal offline dubbing; Local and Colab Live choices. Modal Live is unavailable.

### Improved

- Timing groups, bounded tempo adjustment, safe resynthesis, audio QA and retry/fallback reporting.
- Responsive GUI, model setup, provider connection, output preview and activity feedback.

### Fixed

- Update CI to install CPU-only test dependencies, run pytest for unittest-style and fixture-based tests, and run the Node browser-extension tests.
- Guard ambiguous speaker identities and rejected speech with explicit fallbacks; keep source-audio restoration in the Live stop workflow. These safeguards do not guarantee quality on every input.

### Testing / Verification

- Saved full suite: 324 passed, 15 skipped, 66 subtests, before final UI work; later targeted UI tests: 7 passed. These are separate historical checkpoints.
- Bounded GUI Colab offline NASA run and short synthetic Modal offline run completed; an earlier Live Colab capture/playback test used Chrome for Testing. New Chrome launcher capture-to-playback is not yet verified end to end. See [release readiness](docs/RELEASE_READINESS.md).

### Known Limitations

- Live remains experimental, with variable delay and no universal quality or latency guarantee. The new Chrome launcher, clean platform installs, local Mac Live inference and larger Hy-MT2 variants need further validation.
- Overlap is flagged, not separated; Community-1 requires gated model access. GUI restart may not recover a running Colab connection's state; cloud resources remain allocated until explicitly disconnected.
- Five demo attachments are public in the [media issue](https://github.com/kadirb4rut/video-dubbing-translator/issues/4). The public issue and release-candidate README render the requested 2×2 layout; the table scrolls horizontally on narrow screens. See [media status](docs/release/DEMO_MEDIA.md).

### Breaking Changes

- The default translation path has changed from the historical Google Translate integration to local Hy-MT2, which requires separate runtime and model setup.

## Earlier unpublished development notes

### Offline dubbing

- Keep the default batch workflow local: Whisper/WhisperX, optional pyannote Community-1 diarization, local Hy-MT2 translation, VoxCPM2 voice cloning, and FFmpeg reconstruction.
- Add stable speaker-aware timing groups and deterministic per-speaker reference selection with the reference transcript passed to VoxCPM2.
- Add duration-aware Hy-MT2 candidate selection and one bounded shorten/expand rewrite, followed by conservative tempo correction.
- Add per-group audio, timing, and speaker-similarity QA reports. A hard audio QA failure may trigger one regeneration; soft warnings remain visible.
- Keep Hy-MT2, Community-1, VoxCPM2, and other model weights outside the source distribution and download them explicitly.

### Live Dubbing

- Keep Live Dubbing separate and **experimental**. Sustained real YouTube behavior, translation quality, and latency remain unverified across sources and hardware.
- Use Google Chrome with isolated application profiles, an automatic DevTools-based Live extension loader, and a fresh Final Video view on GUI start. Browser setup does not download model weights; clean installation and full live dubbing remain pending end-to-end verification.
- Do not claim a universal p95 below 15 seconds. The two-chunk TTS scheduler lease candidate is excluded from the stable tree because Run30/Run31 do not establish a controlled, reliable benefit.

### Compute location selection

- Add Local, Colab CLI/G4 and full-pipeline Modal choices for offline work; Live supports Local or Colab CLI. Keep the legacy Modal TTS-only route separate.
- Keep provider credentials in their own CLIs; save non-secret provider/session/project or managed Modal app/function identifiers.
- Add GUI account authorization/preparation, Colab batch transfer and authenticated loopback-only Live tunnels. The dedicated G4 is reused or created by the GUI; missing cloud models are prepared explicitly on connection.
- Keep cloud compute connected until **Disconnect GPU**. Job completion, error/cancel, browser closure and GUI closure do not request shutdown. Persistent caches can incur separate storage charges.
- Record the real Colab NASA full-video run, short full-pipeline Modal run and explicit Modal Disconnect, plus bounded Colab Live capture. Persistent Drive setup, clean platform installation and Modal Live remain unverified/unavailable as detailed in release readiness.

### Web GUI design and feedback — 2026-11-01

- Put Live and Offline controls in distinct areas of the same main panel; keep output/logs beside them on desktop and stack on narrow screens.
- Add a lighter blue theme, clearer actions, collapsed advanced settings, visible in-page errors and activity feedback driven by existing server states. Waiting animation does not imply a measured percentage or ETA.
- Apply compact desktop sizing: 36 px controls, 14 px body text, 16 px panel padding and a 1280 px content maximum; retain larger touch targets.
- Record 7 targeted post-redesign tests and desktop/narrow browser checks separately from the earlier full-suite run. See `docs/release/UI_UX_VERIFICATION_20261101.md`.

### Documentation and release preparation

- Document reproducible installation, CLI/GUI setup, local model management, architecture, real E2E evidence, hardware limits, licensing, and third-party notices.
- Prepare local-only GitHub Release and VoxCPM community Discussion drafts. No GitHub action is authorized by this changelog.
- Keep development media local until redistribution permission is recorded.
