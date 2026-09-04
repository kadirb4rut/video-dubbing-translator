# Model and media license gate

This project keeps provider code behind adapters so a model can be disabled without changing the API contract. As of 2026-08-19, the upstream [Chatterbox repository](https://github.com/resemble-ai/chatterbox) and [ResembleAI model card](https://huggingface.co/ResembleAI/chatterbox) identify Chatterbox Multilingual V3 as the current general-purpose multilingual model and publish MIT licensing; the model card lists 23 languages and says generated audio carries the built-in Perth watermark. The worker pins the current `chatterbox-tts==0.1.7` package release used by the verified smoke environment. This supports the provider selection, but the exact checkpoint snapshot and downstream dependency notices still need to be recorded in the release manifest before public commercial use. Demucs code and each downloaded checkpoint must be audited separately. Whisper, DeepFilterNet, optional lip-sync models, FFmpeg builds, fonts, and UI assets also require a per-artifact review.

The repository deliberately does not bundle model weights. `config/cost_profiles.json` is marked `measured: false` and is a development seed, not a production price or performance claim. The current provider audit status is:

- Whisper: the [official code/license](https://github.com/openai/whisper/blob/main/LICENSE) is MIT; record the exact downloaded checkpoint and runtime dependency notices.
- Demucs: the [official repository](https://github.com/facebookresearch/demucs) states the code is MIT; each downloaded checkpoint/model bundle still requires separate review.
- DeepFilterNet 0.5.6: the [official project](https://github.com/Rikorose/DeepFilterNet) publishes Apache-2.0 project terms; review the pinned package, native library, and downloaded model artifacts together.
- Chatterbox Multilingual V3: MIT upstream/model-card status and built-in watermark are recorded above; pin the exact Hugging Face snapshot and audit downstream artifacts before public commercial voice cloning.
- FFmpeg, fonts, icons, and any future lip-sync provider: review the exact distributed artifact and its terms.

No capability is considered commercially cleared merely because its Python package installs. The GPU worker deliberately omits Chatterbox's optional Gradio UI and overrides its vulnerable dependency pins with the reviewed runtime set in `requirements.worker-gpu.txt`; this is a compatibility/security measure, not a license clearance. Re-run the provider import, real media benchmark, dependency audit, and exact checkpoint manifest review for every release.
