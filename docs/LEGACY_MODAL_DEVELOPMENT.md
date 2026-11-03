# Historical Modal development serving

For a remotely hosted GPU worker, install only the small local control-plane
dependency from `requirements-modal.txt`, configure Modal with
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`, and run:

```bash
MODAL_LOGLEVEL=WARNING .venv/bin/modal run modal_app.py
```

`modal_app.py` mounts a persistent VoxCPM2 snapshot, requests
`RTX-PRO-6000` first (with documented fallbacks), keeps one model warm per
container, exposes complete WAV and native streaming PCM functions, and
scales idle containers down after 60 seconds. The short real validation
record is [`temp/modal-serving-validation.json`](../temp/modal-serving-validation.json);
it covers normal TTS, a real `SPEAKER_00.wav` clone, prompt-cache reuse,
streaming TTFA/RTF, and two-request concurrency.

The optional [`modal_vllm_smoke.py`](../modal_vllm_smoke.py) probes the
OpenAI-compatible vLLM-Omni VoxCPM2 path in an isolated Python 3.12 image
(`requirements-vllm-omni.txt`). It currently reaches imports but fails during
engine-core model startup on the tested image; the native `modal_app.py`
backend is the validated fallback. See the exact version/preflight/traceback
record in [`temp/modal-vllm-omni-validation.json`](../temp/modal-vllm-omni-validation.json).
This record describes an earlier serving-only gate, not the current Live Dubbing acceptance requirements.

This historical TTS-only backend is retained for compatibility. The current GUI also offers a separate full-pipeline Modal offline app, verified with a short synthetic video and explicit Disconnect on 2026-11-01; see [compute providers](COMPUTE_PROVIDERS.md) and [the provider test record](release/GUI_COMPUTE_E2E_20261101.md). Neither Modal route implements the current Live Dubbing backend.
