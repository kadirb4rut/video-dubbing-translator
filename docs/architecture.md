# LingoWave architecture notes

## Product boundary

The existing `Video_Translator.py` and local `web_gui.py` remain available for local development and output comparison. The SaaS surface is a separate React application and an API boundary; GPU code does not run in the web process.

## Provider boundary

The API submits an immutable job spec. Workers resolve configured implementations through the provider protocols in `backend/app/providers.py`. This keeps Whisper/WhisperX, a commercial-safe multilingual TTS provider, Demucs, DeepFilterNet, and optional LatentSync replaceable.

## Billing boundary

Credits are an auditable ledger, not a mutable balance supplied by the browser. A job reserves the server-side estimate, finalizes only after output validation, and releases the reservation on infrastructure failure. Stripe webhook reconciliation belongs beside the ledger transaction boundary.

## Commercial readiness checklist

- Confirm the selected TTS model and checkpoint license for commercial SaaS before enabling voice cloning.
- Audit Whisper/WhisperX, Demucs code and checkpoints, noise-removal checkpoints, LatentSync, FFmpeg distribution, fonts, and icon assets.
- Store consent records and deletion events for reference voices.
- Enforce upload MIME/size validation and safe subprocess argument arrays.
- Add rate limits, abuse reporting, public-figure protections, and private signed downloads before launch.
