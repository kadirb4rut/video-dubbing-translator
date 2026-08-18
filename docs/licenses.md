# Model and media license gate

This project keeps provider code behind adapters so a model can be disabled without changing the API contract. Chatterbox Multilingual is the intended multilingual voice provider, but its exact model artifacts and downstream dependencies still need to be recorded in the release manifest before public commercial use. Demucs code and each downloaded checkpoint must be audited separately. Whisper, DeepFilterNet, optional lip-sync models, FFmpeg builds, fonts, and UI assets also require a per-artifact review.

The repository deliberately does not bundle model weights. `config/cost_profiles.json` is marked `measured: false` and is a development seed, not a production price or performance claim.
