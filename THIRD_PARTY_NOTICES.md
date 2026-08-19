# Third-party notices

This file is an engineering inventory, not legal advice. The root `LICENSE` applies to original Video Dubbing Translator code. It does not relicense third-party source, Python packages, model weights, services, or generated outputs.

No third-party model weights or FFmpeg binaries are committed to this repository.

## Bundled source

### vocal-remover

- Location: `vocal-remover/`
- Upstream: <https://github.com/tsurumeso/vocal-remover>
- Audited upstream revision: `99f92fe4b6bfe37bf4ff5bf4110ce224007312e5`
- License: MIT, copyright 2019 tsurumeso
- Local changes: the vendored files match that upstream revision except for a missing-checkpoint error guard in `vocal-remover/inference.py`.
- License text: `vocal-remover/LICENSE`

The upstream copyright and license are preserved. This code is not represented as original Video Dubbing Translator work.

## Downloaded models and external model assets

| Component | Source | Terms found during audit | Practical effect |
| --- | --- | --- | --- |
| vocal-remover `baseline.pth` | [`fabiogra/baseline_vocal_remover`](https://huggingface.co/fabiogra/baseline_vocal_remover) at revision `0206a40f1c92aa7caa8303e022906e3da0d87fb0` | Hugging Face metadata marks it MIT. | Downloaded separately and checksum-verified; not covered by the root license. |
| OpenAI Whisper models | [`openai/whisper`](https://github.com/openai/whisper) | MIT for code and model weights. | Downloaded on first use. |
| WhisperX alignment models | Selected dynamically by language in WhisperX | Terms vary by torchaudio bundle or Hugging Face model. | Review the selected model's card/license before redistribution; no single repository-wide license can cover every alignment model. |
| VoxCPM2 model, tokenizer, and AudioVAE | [`openbmb/VoxCPM2`](https://huggingface.co/openbmb/VoxCPM2) at revision `32279effe8c19989596f05d353d1447f51d9e915` | Apache-2.0 in repository metadata and the official model card; the card describes commercial use as permitted. | This is the default voice model. The pinned snapshot contains the model weights, tokenizer, and built-in 48 kHz AudioVAE; no separately licensed vocoder or denoiser is loaded by the app. The license does not grant rights in a person's voice or excuse deceptive use. |
| LatentSync 1.6 checkpoints | [`ByteDance/LatentSync-1.6`](https://huggingface.co/ByteDance/LatentSync-1.6) at revision `c42c7e6c8e9c213626389fa7d9a3c444b8536353` | Model repository metadata marks the checkpoints OpenRAIL++. | Optional, downloaded separately, and subject to use-based restrictions independent of the Apache-2.0 source license. |

## Major code dependencies and tools

| Component | Version/source used | License finding | Notes |
| --- | --- | --- | --- |
| VoxCPM | [`OpenBMB/VoxCPM`](https://github.com/OpenBMB/VoxCPM) package `voxcpm==2.0.3`; audited source revision `ee8161e9e1b7b082cb5721a3a9980da4204401e6` | Apache-2.0 | Official inference implementation. The app disables the optional external ZipEnhancer denoiser. Upstream acknowledges MiniCPM-4, CosyVoice, and Descript Audio Codec; the shipped official inference code/model repository applies Apache-2.0, and Descript Audio Codec is MIT. |
| Whisper | `openai-whisper==20250625` | MIT | Code and official weights are MIT. |
| WhisperX | `whisperx==3.3.1` | BSD-2-Clause license file | The wheel's metadata says MIT while its included `LICENSE` is BSD-2-Clause; this project follows the included license file conservatively. |
| Faster-Whisper / PyAV | `faster-whisper==1.1.0` / `av==14.2.0` | MIT / BSD-3-Clause | Versions are pinned together to use a supported Apple-silicon PyAV wheel and avoid compiling an older PyAV against FFmpeg 8. |
| deep-translator | `deep-translator==1.11.4` | Upstream tag/wheel `LICENSE` is Apache-2.0; package metadata says MIT | Upstream metadata is inconsistent. The package is installed externally, not vendored. Recheck before redistributing its wheel. |
| Google Translate endpoint | Used by `deep-translator.GoogleTranslator` | External service terms, not an open-source license | Transcript text leaves the machine. Availability, rate limits, and permitted use are controlled by the service provider. |
| MoviePy | `moviepy==1.0.3` | MIT | Installed dependency. |
| FFmpeg | System executable | LGPL-2.1-or-later by default; optional components can make a build GPL-2.0-or-later | The app calls the user's FFmpeg executable and does not distribute it. Packaging an FFmpeg binary, especially one with `libx264`/GPL components, requires a separate compliance review. |
| LatentSync source | revision `a229c3948406bc2cf6eaf4873e662e70c6a04746` | Apache-2.0 | Cloned into ignored `third_party/LatentSync/`; not committed here. Its own acknowledgements identify additional upstream components. |
| PyTorch / torchaudio | `2.5.1` | BSD-style | Installed dependencies. |
| TorchCodec | `0.1.1` | BSD-3-Clause | Declared by VoxCPM and version-matched to PyTorch 2.5. VoxCPM's active generation path does not import it. Its optional native decoder supports FFmpeg 4–7, while the application performs media decoding with the system FFmpeg executable. |
| Transformers | `4.51.3` | Apache-2.0 | Pinned because later resolution was incompatible with the project's NumPy 1.22 runtime at import time. |
| requests | `2.32.3` | Apache-2.0 | Installed dependency. |
| huggingface-hub | `>=0.30.2,<1.0` | Apache-2.0 | Installed dependency. |
| hf-transfer | `0.1.9` | Apache-2.0 | Enables resumable accelerated model setup when `HF_HUB_ENABLE_HF_TRANSFER=1`. |
| librosa / resampy | `0.10.0` / `0.4.3` | ISC | Installed dependencies. |
| matplotlib | `3.8.4` | PSF-based license | Installed dependency. |
| OpenCV Python | `4.10.0.84` | Apache-2.0 | Installed dependency. |
| SoundFile | `0.12.1` | BSD-3-Clause | Installed dependency; the underlying libsndfile has its own LGPL terms when distributed. |
| tqdm | `4.66.6` | MPL-2.0 and MIT | Installed dependency. |
| NumPy | `1.22.0` | BSD-3-Clause | Installed dependency. |
| fsorter | `2.4` | MIT metadata | Installed dependency. |
| Setuptools | `80.9.0` | MIT | Kept below 81 because the pinned Librosa/vocal-remover path still imports the legacy `pkg_resources` module. |

Transitive dependencies have their own notices and are not exhaustively reproduced here. Use the resolved environment's package metadata and distribution license files when preparing a binary distribution.

## Audit conclusion

A root MIT license is practical for the original application code because the only bundled third-party source has a preserved, compatible MIT license and external dependencies are not copied into the repository. The default speech-generation path uses commercially permissive code and model licenses and has no model-level non-commercial output restriction. That does **not** make the complete stack or its outputs MIT-licensed.

The main unresolved human/legal decisions are:

1. Whether a future packaged distribution will bundle FFmpeg, Python wheels, or model weights; each bundled artifact needs a distribution-specific compliance review.
2. Which language-specific WhisperX alignment models will be supported and redistributed, if any.
3. Whether the external Google Translate workflow is acceptable for the intended data, privacy, commercial use, and service-terms context.
4. Whether the user has the consent and personality/voice rights required for the source material and intended generated output.
