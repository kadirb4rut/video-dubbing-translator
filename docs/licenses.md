# Model and media license gate

This project keeps provider code behind adapters so a model can be disabled without changing the API contract. The current voice provider is pinned VoxCPM2 (`openbmb/VoxCPM2`) at revision `32279effe8c19989596f05d353d1447f51d9e915`. This file records integration metadata only; it is not a commercial-use clearance.

The repository deliberately does not bundle model weights. `config/cost_profiles.json` contains evidence-backed internal credit rates from the real CPU acceptance/Chrome validation runs; those rates are not commercial price, margin, model-performance, or license-clearance claims. The optional lip-sync profile is explicitly disabled until a provider is configured. The current provider inventory is:

- Whisper: exact downloaded checkpoint and runtime dependency notices must be recorded per release.
- Demucs: code and each downloaded checkpoint/model bundle require separate review.
- DeepFilterNet 0.5.6: review the pinned package, native library, and downloaded model artifacts together.
- VoxCPM2: the exact model revision is pinned above and in `config/model_release_manifest.example.json`; downstream artifacts still require review.
- FFmpeg, fonts, icons, and any future lip-sync provider: review the exact distributed artifact and its terms.

No capability is considered commercially cleared merely because its Python package installs. Re-run the provider import, real media benchmark, dependency audit, and exact checkpoint manifest review for every release.

LICENSE REVIEW: NOT PERFORMED — USER WILL REVIEW BEFORE PRODUCTION
