<div align="center">

# Video Dubbing Translator

**Turn a source video into a translated, voice-cloned dub from a local browser interface.**

[![Python 3.10](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/Code-MIT-green.svg)](LICENSE)
[![XTTS: non-commercial](https://img.shields.io/badge/XTTS--v2-CPML_non--commercial-orange.svg)](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)
[![FFmpeg required](https://img.shields.io/badge/FFmpeg-required-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/download.html)

**video → audio separation → transcription → translation → voice cloning → reconstruction → optional lip-sync**

[Quick start](#quick-start) · [Demo](#demo--turn-sound-on) · [How it works](#how-it-works) · [Limitations](#limitations) · [License](#license-and-third-party-terms)

</div>

Video Dubbing Translator is a local-first Python application for dubbing videos into another language. Whisper and WhisperX transcribe and align speech, `deep-translator` sends transcript text to Google Translate, Coqui XTTS-v2 generates speech from source-voice references, and FFmpeg/MoviePy rebuild the video. LatentSync can optionally add a CUDA-only lip-sync pass.

> [!IMPORTANT]
> This is **local-first, not fully offline**. Media processing and AI inference run on your machine, but translation sends transcript text to Google Translate. Initial setup also downloads models. XTTS-v2 and its outputs are restricted to non-commercial use under the [Coqui Public Model License](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt).

## Demo — turn sound on

Compare a 15-second highlight in its original English form and the generated Turkish dub. GitHub serves release assets as downloads, so open each link in a media player and make sure sound is enabled.

| Original English input | Turkish dubbed output |
| :---: | :---: |
| [▶ Play original highlight with sound](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/input-highlight-15s.mp4) | [▶ Play dubbed highlight with sound](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/target-highlight-15s.mp4) |

Prefer the complete 59.93-second comparison? Download the [full original](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/input.mp4) and [full Turkish dub](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/target.mp4), or [view the demo release and file details](https://github.com/kadirb4rut/video-dubbing-translator/releases/tag/demo-videos).

## Key features

- Local browser GUI with upload, language selection, model setup, live logs, cancellation, and output playback.
- Vocal/background separation using a vendored MIT-licensed `vocal-remover` snapshot.
- Whisper transcription plus WhisperX word alignment.
- Translation across the 12 language choices exposed by the app.
- Per-segment voice cloning and multilingual speech generation with XTTS-v2.
- FFmpeg/MoviePy video reconstruction with the original background track.
- Optional LatentSync 1.6 lip-sync on a high-memory NVIDIA GPU.
- CLI entry point for scripted runs.
- Pinned model revisions, baseline-model checksum verification, and a setup preflight.

## Quick start

### 1. Prerequisites

- **Python 3.10 exactly.** The pinned Coqui/WhisperX stack is not validated on other Python versions.
- **Git** for cloning the repository and optional LatentSync setup.
- **FFmpeg and ffprobe** available on `PATH`.
- At least **10 GB of free disk space** for the environment, caches, and models.

Install FFmpeg:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update
sudo apt install ffmpeg
```

On Windows, install an FFmpeg build from the [official FFmpeg download page](https://ffmpeg.org/download.html#build-windows), add its `bin` directory to `PATH`, and confirm that both `ffmpeg -version` and `ffprobe -version` work in a new terminal.

### 2. Clone and install

macOS / Linux:

```bash
git clone https://github.com/kadirb4rut/video-dubbing-translator.git
cd video-dubbing-translator
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
git clone https://github.com/kadirb4rut/video-dubbing-translator.git
cd video-dubbing-translator
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dependency set is large. On Apple Silicon, PyAV may build locally and needs the Homebrew FFmpeg libraries. NVIDIA users who want CUDA acceleration should install the matching PyTorch 2.5.1 CUDA build using the [official PyTorch selector](https://pytorch.org/get-started/locally/) before installing the remaining requirements.

### 3. Read the model terms and download models

The default voice model is XTTS-v2. Its [CPML terms](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt) allow only non-commercial use of the model and its outputs. After reading them, pass the explicit acceptance flag if you agree:

```bash
python scripts/setup_models.py --accept-xtts-cpml
```

This downloads the XTTS-v2 files and a pinned 57 MB vocal-remover checkpoint. The latter is verified against SHA-256 `f0bf9cb226e20571aac8aeda9f6d5f70e495c7b9b3457afe4b11cfec3b515fc3`.

### 4. Run the preflight

```bash
python scripts/check_setup.py
```

Resolve every `[FAIL]` item before starting a dub. `[WARN]` items describe optional components or performance limitations.

### 5. Start the GUI

```bash
python web_gui.py
```

Or use `./start_gui.sh` on macOS/Linux, `start_gui.command` on macOS, or `start_gui.bat` on Windows. The app opens at [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Usage

1. Upload a short test video.
2. Select the source language or leave **Auto Detect** enabled.
3. Select the target language.
4. Use **Download / Check Required Models** if setup was not already completed. The GUI requires explicit CPML acceptance.
5. Leave LatentSync disabled unless its separate CUDA environment is ready.
6. Start dubbing and follow the process log.
7. Play the final video in the GUI or open the output folder.

Outputs are written to `vocal-remover/final_video/final/`. Intermediate media and model files are ignored by git.

### CLI

```bash
python scripts/run_pipeline.py input.mp4 --source-language auto --target-language tr
```

Add `--lip-sync` only after completing the optional LatentSync setup.

## How it works

```text
Input video
  └─ FFmpeg audio extraction
      └─ vocal-remover: vocals + instrumental track
          └─ Whisper transcription
              └─ WhisperX word alignment
                  └─ punctuation-based speech segments
                      └─ Google Translate via deep-translator (network request)
                          └─ XTTS-v2 voice cloning per segment
                              └─ duration adjustment + video reconstruction
                                  └─ original instrumental track mixed back in
                                      └─ optional LatentSync lip-sync
```

## Supported languages

The current UI exposes auto-detection plus these target/source codes:

`en`, `it`, `es`, `fr`, `de`, `pt`, `ja`, `ru`, `tr`, `nl`, `pl`, `cs`

Successful dubbing also depends on Google Translate, WhisperX alignment-model availability, and XTTS-v2 language support. A code appearing in the UI does not guarantee equal quality across every stage.

## Platforms and hardware

| Environment | Base dubbing | Notes |
| --- | --- | --- |
| macOS, including Apple Silicon | Supported but CPU-heavy | The current pipeline does not use MPS for Whisper/XTTS; expect long runtimes. |
| Linux + NVIDIA CUDA | Recommended | Whisper and XTTS use CUDA when PyTorch detects it. |
| Windows + NVIDIA CUDA | Supported with manual prerequisites | Use Python 3.10, FFmpeg on `PATH`, and a matching CUDA-enabled PyTorch build. |
| CPU-only Linux/Windows | Possible for short clips | Model inference can be very slow and memory-intensive. |

The base pipeline has no hard CUDA requirement. LatentSync does.

## Optional LatentSync 1.6

LatentSync is kept separate from the main environment because its dependency stack is large and CUDA-specific. Version 1.6 requires approximately **18 GB of VRAM** for inference and downloads a checkpoint of roughly 5 GB.

```bash
python scripts/setup_latentsync.py
cd third_party/LatentSync
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. If the LatentSync interpreter is elsewhere, point the main app to it before launch:

```bash
export LATENTSYNC_PYTHON=/absolute/path/to/latentsync/python
```

Windows PowerShell:

```powershell
$env:LATENTSYNC_PYTHON = "C:\absolute\path\to\LatentSync\.venv\Scripts\python.exe"
```

The setup script pins source revision `a229c3948406bc2cf6eaf4873e662e70c6a04746` and model revision `c42c7e6c8e9c213626389fa7d9a3c444b8536353`. LatentSync source is Apache-2.0; its Hugging Face checkpoints are marked OpenRAIL++.

## First-run downloads and network access

- Model setup downloads vocal-remover and XTTS-v2 files.
- The first transcription downloads the Whisper `base` model.
- WhisperX may download a language-specific alignment model.
- Translation sends each speech segment to Google Translate through `deep-translator`.
- Optional LatentSync downloads several gigabytes of source/checkpoint data.

After caches are populated, transcription, voice generation, vocal separation, and reconstruction are local. Translation still requires network access.

## Limitations

- XTTS-v2 and its outputs are non-commercial under CPML; this is not an end-to-end permissively licensed stack.
- Translation is an external, unofficial Google Translate integration and may be rate-limited, changed, or unavailable.
- Segmentation is punctuation-based. Long, noisy, overlapping, or weakly punctuated speech can produce poor timing.
- There is no speaker diarization; multi-speaker and overlapping-dialogue results are not guaranteed.
- Voice cloning quality depends heavily on clean source vocals and segment length.
- Generated speech is time-adjusted to the original segment, which can sound compressed on large translation-length differences.
- The application has no authentication and is intentionally bound to `127.0.0.1`; do not expose it as a public web service.
- Full inference needs large model downloads and substantial RAM. CPU-only runs can take a long time.

## Troubleshooting

**`python scripts/check_setup.py` reports the wrong Python version**

Delete and recreate `.venv` with Python 3.10. Merely installing Python 3.10 does not change an existing environment.

**FFmpeg is missing**

Confirm both `ffmpeg -version` and `ffprobe -version` succeed in the same activated terminal used to start the app.

**A model download is rejected**

For XTTS-v2, read the CPML and use the explicit acceptance flow. For a baseline checksum failure, remove only `vocal-remover/models/baseline.pth` and rerun model setup.

**CUDA is installed but not detected**

Run `python -c "import torch; print(torch.cuda.is_available())"`. If it prints `False`, install a PyTorch 2.5.1 build matching the machine's CUDA setup.

**Translation fails or stalls**

Check network access. `deep-translator` relies on an external Google endpoint and can be rate-limited; retry with a short clip.

**LatentSync is unavailable**

Keep it disabled for base dubbing. It requires a separate environment, an NVIDIA CUDA GPU, and about 18 GB VRAM for the pinned 1.6 setup.

## Project layout

```text
Video_Translator.py          core dubbing pipeline
web_gui.py                   local browser GUI
video_translator_gui.py      legacy Tkinter GUI
scripts/check_setup.py       read-only environment preflight
scripts/setup_models.py      required model setup and CPML acceptance
scripts/download_model.py    pinned/checksummed vocal model download
scripts/run_pipeline.py      CLI orchestration and optional lip-sync
scripts/setup_latentsync.py  pinned optional LatentSync setup
vocal-remover/               vendored upstream MIT snapshot
```

## Security, privacy, and responsible use

- Use only media and voices you have the right and consent to process.
- Clearly disclose synthetic or translated speech where context could mislead viewers.
- Uploaded videos, extracted speech, transcripts, reference clips, and outputs stay on local disk, but transcript text is sent to Google Translate.
- Do not commit media, transcripts, model weights, or environment files.
- Keep the GUI on localhost. It has no authentication, upload quota, or multi-user isolation.
- Report security issues using [SECURITY.md](SECURITY.md).

## Roadmap

- Replace the external translation step with an optional local backend.
- Add automated tests around segmentation and output-path handling.
- Improve per-stage progress and cleanup controls.
- Add configurable Whisper and XTTS models.
- Evaluate an openly licensed voice model for a fully permissive default stack.
- Add speaker diarization and better multi-speaker handling.

## Contributing

Issues and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the pipeline, and include validation details plus any new model/data license terms in the pull request.

## License and third-party terms

Original project code is available under the [MIT License](LICENSE). That license does **not** replace the licenses of dependencies, downloaded models, services, or the separately licensed vendored `vocal-remover/` code.

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistribution or commercial use. In particular:

- `vocal-remover/` retains its upstream MIT license.
- Coqui TTS code is MPL-2.0.
- XTTS-v2 and its outputs are CPML **non-commercial**.
- LatentSync source is Apache-2.0; its downloaded checkpoints are marked OpenRAIL++.
- FFmpeg licensing depends on the installed build and enabled components.
- Alignment-model licenses vary by language/model.

This repository does not provide legal advice. If you plan to redistribute a packaged application, bundle model weights, or use generated output commercially, obtain your own legal review.
