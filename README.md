<div align="center">

# Video Dubbing Translator

**Dub a video into another language while keeping the source voice and the original scene.**

Local-first, open-source video dubbing with reference-voice cloning, translation, and optional lip-sync.

[![CI](https://github.com/kadirb4rut/video-dubbing-translator/actions/workflows/static-checks.yml/badge.svg)](https://github.com/kadirb4rut/video-dubbing-translator/actions/workflows/static-checks.yml)
[![Python 3.10](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/Code-MIT-green.svg)](LICENSE)
[![VoxCPM2: Apache 2.0](https://img.shields.io/badge/VoxCPM2-Apache--2.0-blue.svg)](https://huggingface.co/openbmb/VoxCPM2)

[Demo](#demo) · [Quick Start](#quick-start) · [How it works](#how-it-works) · [Platforms](#platforms-and-hardware) · [Contributing](CONTRIBUTING.md)

</div>

Video Dubbing Translator is a local-first Python application with a browser GUI and CLI. It separates vocals from the background, transcribes and aligns speech, translates the transcript, clones a reference voice with VoxCPM2, and rebuilds a shareable dubbed video. The base workflow runs media processing and inference locally; translation currently uses Google Translate through `deep-translator`.

> [!IMPORTANT]
> **Local-first is not fully offline.** Transcript text is sent to Google Translate. First-run setup also downloads models. Do not expose the localhost GUI as a public service, and only process media and voices you have permission to use.

## Why this project

- **Understandable output:** keep the original background track while replacing speech.
- **Reference-voice cloning:** synthesize translated segments with the source speaker's voice reference using VoxCPM2.
- **Inspectable stages:** separate, transcribe, align, translate, synthesize, fit timing, and reconstruct instead of hiding everything behind one opaque request.
- **Practical defaults:** base dubbing does not require LatentSync or a CUDA GPU, although inference is much faster with one.
- **Open source:** MIT-licensed project code with pinned model revisions and third-party notices.

## Demo

Turn sound on and compare the same 15-second highlight before and after the current VoxCPM2 pipeline:

<table align="center" width="820">
  <tr>
    <td align="center" width="410"><strong>Original — English</strong><br><sub>Source voice and timing</sub></td>
    <td align="center" width="410"><strong>AI dub — Turkish</strong><br><sub>VoxCPM2 reference-voice output</sub></td>
  </tr>
  <tr>
    <td align="center" width="410"><video controls width="360" src="https://github.com/user-attachments/assets/ce74aa39-c683-431e-b15b-2153a1495384"></video></td>
    <td align="center" width="410"><video controls width="360" src="https://github.com/user-attachments/assets/c993b06d-cf55-48e6-b91b-9a7530d60734"></video></td>
  </tr>
</table>

Prefer one file for sharing? Download the generated [single-file comparison](docs/assets/demo-comparison-15s.mp4), or reproduce it with [`scripts/create_demo_comparison.sh`](scripts/create_demo_comparison.sh). The source clips and the current output remain available on the [demo release](https://github.com/kadirb4rut/video-dubbing-translator/releases/tag/demo-videos).

Direct files: [original English clip](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/input-highlight-15s.mp4) · [VoxCPM2 Turkish dub](https://github.com/kadirb4rut/video-dubbing-translator/releases/download/demo-videos/target-voxcpm2-highlight-15s.mp4)

## Quick Start

### Prerequisites

| Requirement | Base dubbing | Notes |
| --- | --- | --- |
| Python 3.10 | Required | The pinned VoxCPM2/WhisperX stack is not validated on other versions. |
| FFmpeg + ffprobe | Required | They must both be on `PATH`; the installer never uses `sudo` to install them. |
| Disk space | 10+ GB recommended | VoxCPM2's pinned snapshot is about 5 GB, in addition to the environment and caches. |
| GPU | Optional | NVIDIA CUDA is recommended; CPU and Apple Silicon paths are supported with different speed/memory tradeoffs. |

Install FFmpeg with your normal system package manager first, for example `brew install ffmpeg` on macOS or `sudo apt install ffmpeg` on Ubuntu/Debian. On Windows, use an FFmpeg build from the [official download page](https://ffmpeg.org/download.html#build-windows) and add its `bin` directory to `PATH`.

### macOS / Linux

```bash
git clone https://github.com/kadirb4rut/video-dubbing-translator.git
cd video-dubbing-translator
./install.sh
./start_gui.sh
```

`install.sh` verifies Python and FFmpeg, creates or reuses `.venv`, installs the pinned dependencies, downloads the required model snapshot, and runs the preflight. To prepare the environment without downloading models yet, use `SKIP_MODELS=1 ./install.sh`; run `./install.sh` again later to finish setup.

### Windows PowerShell

```powershell
git clone https://github.com/kadirb4rut/video-dubbing-translator.git
cd video-dubbing-translator
.\install.ps1
.\start_gui.bat
```

Use `.\install.ps1 -SkipModels` when you only need the environment and static checks. The scripts do not silently install system software or change global Python settings.

The browser GUI opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). Upload a short test video, choose the target language, leave LatentSync disabled for the first run, and follow the process log.

## Manual Installation / Advanced Setup

If you prefer to control each step:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts/setup_models.py
python scripts/check_setup.py
python web_gui.py
```

Windows PowerShell uses `py -3.10`, `.venv\Scripts\Activate.ps1`, and the same Python commands. NVIDIA users can install a matching PyTorch CUDA build from the [official selector](https://pytorch.org/get-started/locally/) before installing the remaining requirements.

### CLI

```bash
python scripts/run_pipeline.py input.mp4 --source-language auto --target-language tr
```

Add `--lip-sync` only after completing the separate LatentSync setup.

## How it works

```text
Input video
  └─ FFmpeg audio extraction
      └─ vocal-remover: vocals + background
          └─ Whisper transcription
              └─ WhisperX word alignment
                  └─ punctuation-based speech segments
                      └─ Google Translate via deep-translator (network request)
                          └─ VoxCPM2 reference-voice cloning per segment (48 kHz)
                              └─ duration adjustment + video reconstruction
                                  └─ original background mixed back in
                                      └─ optional LatentSync lip-sync
```

The demo's English → Turkish run uses real vocal separation, Whisper/WhisperX timing, Google translation, four VoxCPM2 segments, duration fitting, and H.264/AAC reconstruction. The final file is about 14.88 seconds with 48 kHz stereo audio; Turkish ASR recovered all four intended sentences. That is an integration check on one short clip, not a broad benchmark.

## Platforms and hardware

| Environment | Base dubbing | What to expect |
| --- | --- | --- |
| macOS / Apple Silicon | Supported | Systems below 16 GB use slower CPU/bfloat16 defaults to avoid MPS memory exhaustion. |
| Linux + NVIDIA CUDA | Recommended | Whisper and VoxCPM2 can use CUDA when the installed PyTorch build detects it. |
| Windows + NVIDIA CUDA | Supported with manual prerequisites | Use Python 3.10, FFmpeg on `PATH`, and a matching CUDA-enabled PyTorch build. |
| CPU-only Linux/Windows | Possible for short clips | Inference is slow and memory-intensive. |

The base pipeline has no hard CUDA requirement. **LatentSync does.** Its separate 1.6 environment needs an NVIDIA GPU with approximately 18 GB VRAM and several additional gigabytes of downloads:

```bash
python scripts/setup_latentsync.py
```

See the script and [the troubleshooting notes](#troubleshooting) before enabling it.

## Local vs network processing

| Stage | Where it runs | Notes |
| --- | --- | --- |
| Media decoding, vocal separation, Whisper, WhisperX, VoxCPM2, reconstruction | Local machine | Requires model downloads and sufficient RAM/disk. |
| Translation | Google Translate via `deep-translator` | Transcript text leaves the machine; availability and rate limits are external. |
| Optional lip-sync | Separate local LatentSync environment | CUDA-only in the supported setup. |

## Limitations and responsible use

- There is no production-grade speaker diarization; overlapping or multi-speaker dialogue is not guaranteed.
- Punctuation-based segmentation and large translation-length changes can affect timing and naturalness.
- Voice cloning depends on clean reference vocals and does not grant rights to impersonate a person.
- The localhost GUI has no authentication, upload quota, or multi-user isolation. Keep it bound to `127.0.0.1`.
- Use only media and voices you have permission and consent to process, and disclose synthetic or translated speech when context could mislead viewers.

## Troubleshooting

**The installer says Python is missing.** Install Python 3.10 and rerun it; an existing virtual environment made with another version must be recreated.

**FFmpeg or ffprobe is missing.** Install both commands with your normal OS package manager and open a new terminal so `PATH` is refreshed.

**Model setup fails.** Check network access and free disk space, then rerun `./install.sh`. Interrupted Hugging Face downloads resume; the vocal-remover checkpoint is checksum-verified.

**Translation stalls.** Google Translate is an external, unofficial integration and may be rate-limited. Retry with a short clip and inspect the preflight output.

**LatentSync is unavailable.** Keep it disabled for base dubbing. It requires its own environment, checkpoint files, and an NVIDIA CUDA GPU.

## Project layout

```text
Video_Translator.py          core dubbing pipeline
web_gui.py                   local browser GUI
install.sh / install.ps1     macOS/Linux and Windows bootstrap
scripts/check_setup.py       read-only preflight
scripts/setup_models.py      pinned required-model setup
scripts/run_pipeline.py      CLI orchestration
scripts/create_demo_comparison.sh  shareable demo generator
voxcpm_runtime.py            pinned VoxCPM2 adapter
vocal-remover/               vendored upstream MIT snapshot
tests/                       dependency-free runtime tests
```

## Roadmap

- Add an optional local translation backend so privacy-sensitive users can avoid the external translation step.
- Add more segmentation and output-path tests, including multi-speaker fixtures that do not contain private media.
- Improve per-stage progress, cleanup controls, and resumable jobs.
- Add configurable Whisper/VoxCPM models and better multi-speaker handling.
- Document a future packaged-launcher path for macOS, Linux, and Windows without bundling fragile multi-gigabyte model stacks.

## Contributing

Focused fixes, documentation improvements, platform notes, and reproducible bug reports are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before changing the pipeline. Good first contributions include improving setup messages, adding dependency-free tests for path/segmentation logic, and documenting verified platform-specific fixes.

If this project is useful to you, a star is an easy way to bookmark it and help other developers discover future improvements. Please star only if you genuinely want to follow the project; there are no artificial engagement tactics here.

See [the release-readiness notes](docs/RELEASE_READINESS.md) for the current source-install release path and the constraints around future packaged launchers.

## License and third-party terms

Original project code is available under the [MIT License](LICENSE). That license does **not** replace the licenses of dependencies, downloaded models, services, or the separately licensed vendored `vocal-remover/` code.

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistribution or commercial use. VoxCPM2 source and the pinned official model snapshot are Apache-2.0; LatentSync source is Apache-2.0 while its checkpoints are marked OpenRAIL++; FFmpeg licensing depends on the installed build and enabled components; WhisperX alignment-model licenses vary by language/model.
