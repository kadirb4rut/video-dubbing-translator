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
| Coqui XTTS-v2 | [`coqui/XTTS-v2`](https://huggingface.co/coqui/XTTS-v2) | [Coqui Public Model License 1.0.0](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt), which allows only non-commercial use of the model and its outputs. | This is the default voice model. The overall default runtime/output cannot be presented as permissively licensed for commercial use. Users must accept the CPML before download. |
| LatentSync 1.6 checkpoints | [`ByteDance/LatentSync-1.6`](https://huggingface.co/ByteDance/LatentSync-1.6) at revision `c42c7e6c8e9c213626389fa7d9a3c444b8536353` | Model repository metadata marks the checkpoints OpenRAIL++. | Optional, downloaded separately, and subject to use-based restrictions independent of the Apache-2.0 source license. |

## Major code dependencies and tools

| Component | Version/source used | License finding | Notes |
| --- | --- | --- | --- |
| Coqui TTS | `TTS==0.22.0` | MPL-2.0 | Code license is separate from XTTS-v2 model terms. |
| Whisper | `openai-whisper==20250625` | MIT | Code and official weights are MIT. |
| WhisperX | `whisperx==3.2.0` | BSD-2-Clause license file | The wheel's metadata says MIT while its included `LICENSE` is BSD-2-Clause; this project follows the included license file conservatively. |
| deep-translator | `deep-translator==1.11.4` | Upstream tag/wheel `LICENSE` is Apache-2.0; package metadata says MIT | Upstream metadata is inconsistent. The package is installed externally, not vendored. Recheck before redistributing its wheel. |
| Google Translate endpoint | Used by `deep-translator.GoogleTranslator` | External service terms, not an open-source license | Transcript text leaves the machine. Availability, rate limits, and permitted use are controlled by the service provider. |
| MoviePy | `moviepy==1.0.3` | MIT | Installed dependency. |
| FFmpeg | System executable | LGPL-2.1-or-later by default; optional components can make a build GPL-2.0-or-later | The app calls the user's FFmpeg executable and does not distribute it. Packaging an FFmpeg binary, especially one with `libx264`/GPL components, requires a separate compliance review. |
| LatentSync source | revision `a229c3948406bc2cf6eaf4873e662e70c6a04746` | Apache-2.0 | Cloned into ignored `third_party/LatentSync/`; not committed here. Its own acknowledgements identify additional upstream components. |
| PyTorch / torchaudio | `2.5.1` | BSD-style | Installed dependencies. |
| requests | `2.32.3` | Apache-2.0 | Installed dependency. |
| huggingface-hub | `>=0.30.2,<1.0` | Apache-2.0 | Installed dependency. |
| librosa / resampy | `0.10.0` / `0.4.3` | ISC | Installed dependencies. |
| matplotlib | `3.8.4` | PSF-based license | Installed dependency. |
| OpenCV Python | `4.10.0.84` | Apache-2.0 | Installed dependency. |
| SoundFile | `0.12.1` | BSD-3-Clause | Installed dependency; the underlying libsndfile has its own LGPL terms when distributed. |
| tqdm | `4.66.6` | MPL-2.0 and MIT | Installed dependency. |
| NumPy | `1.22.0` | BSD-3-Clause | Installed dependency. |
| fsorter | `2.4` | MIT metadata | Installed dependency. |

Transitive dependencies have their own notices and are not exhaustively reproduced here. Use the resolved environment's package metadata and distribution license files when preparing a binary distribution.

## Audit conclusion

A root MIT license is practical for the original application code because the only bundled third-party source has a preserved, compatible MIT license and external dependencies are not copied into the repository. That does **not** make the complete AI stack or its outputs MIT-licensed.

The main unresolved human/legal decisions are:

1. Whether the project's intended uses are compatible with XTTS-v2's non-commercial CPML restriction.
2. Whether a future packaged distribution will bundle FFmpeg, Python wheels, or model weights; each bundled artifact needs a distribution-specific compliance review.
3. Which language-specific WhisperX alignment models will be supported and redistributed, if any.
4. Whether the external Google Translate workflow is acceptable for the intended data, privacy, and service-terms context.
