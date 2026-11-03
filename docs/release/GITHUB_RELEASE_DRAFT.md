# v0.1.0 — Live Dubbing, Hy-MT2 & Multi-Speaker VoxCPM2

The maintainer confirmed media rights and approved the public demo uploads.

Video Dubbing Translator brings speaker-aware offline dubbing and experimental browser-based Live Dubbing together in one GUI. Hy-MT2 translates locally on the selected compute, while Community-1 and VoxCPM2 support speaker-specific references. Duration-aware refinement, bounded timing correction and audio QA help protect output quality.

## Highlights

- Experimental YouTube Live and supported VOD capture with user pairing and automatic extension loading in a separate Chrome profile.
- Offline Local, Colab CLI G4 and full-pipeline Modal choices; Live supports Local and Colab, not Modal. Cloud jobs transfer media or captured audio to the selected provider.
- Context and glossary support, optional speaker diarization, safe reference fallback, synthesis retry and timing safeguards.
- Refreshed GUI, explicit model downloads and separate model runtimes.

## Reproducible quick start

Install Python 3.10 and FFmpeg/ffprobe, then on macOS/Linux:

```bash
git clone https://github.com/kadirb4rut/voxadub.git
cd voxadub
./install.sh
./start_gui.sh
```

On Windows use `.\install.ps1` and `.\start_gui.bat` from PowerShell. See [README prerequisites and platform caveats](https://github.com/kadirb4rut/voxadub/blob/main/README.md#quick-start). Select and explicitly download Hy-MT2 1.8B in the GUI. For Community-1, accept the [model conditions](https://huggingface.co/pyannote/speaker-diarization-community-1) and supply a Hugging Face read token for initial download. Other models and language alignment assets may also require downloads. Keep the GUI on loopback.

### Offline

Select Offline Dubbing, choose Local, Colab CLI or Modal, select the video and languages, then start dubbing. Cloud authentication is interactive and media is transferred to the chosen provider. Cloud compute remains allocated until **Disconnect GPU**; check provider state after a GUI restart. See [compute providers](https://github.com/kadirb4rut/voxadub/blob/main/docs/COMPUTE_PROVIDERS.md).

### Live

Prepare the required models and Live dependencies, choose Local or Colab, and click **Open Live Dubbing Browser**. Open the authorized YouTube source, open the extension side panel, create and enter the one-use pairing code, select languages, and explicitly authorize and start capture. Finish/drain and stop when done. See the complete [Live setup and permissions](https://github.com/kadirb4rut/voxadub/blob/main/docs/LIVE_DUBBING_SETUP.md#pair-and-start-a-session).

### Demos

The [public demo issue](https://github.com/kadirb4rut/voxadub/issues/4) hosts the complete Live recording and four 30-second Offline clips from 00:20–00:50. The public issue renders the four Offline players in a 2×2 table. The release-candidate README renders the same players and 2×2 layout on GitHub; on narrow screens, the table scrolls horizontally. Both offline intervals are retained locally. The [historical demo release](https://github.com/kadirb4rut/voxadub/releases/tag/demo-videos) remains unchanged and predates these features.

## Verification and limits

A full 228.744-second NASA-to-Turkish GUI/Colab offline run and a short synthetic Modal offline run were recorded. An earlier bounded Colab Live capture/playback used Chrome for Testing; the newer Chrome launcher was checked through extension loading and source playback, but its complete capture-to-dubbed-playback path is not established. Saved regression checkpoints include 324 passed/15 skipped/66 subtests before final UI edits and 7 targeted post-redesign tests. A later isolated local Python 3.10 run and the GitHub-hosted Linux release-candidate run each reported 334 passed and 16 skipped; CI also passed all three Node extension test files. The 16 optional tests require Silero assets or the English syntax model. See [release readiness](https://github.com/kadirb4rut/voxadub/blob/main/docs/RELEASE_READINESS.md) for scope and dates.

Live latency varies and quality is not guaranteed. Overlapping speakers are not separated. Hy-MT2 7B/30B, clean Windows/Linux installs and local Mac Live inference were not validated here. Colab connection recovery after GUI restart is incomplete. Verify provider state and explicitly disconnect cloud resources to avoid continuing charges.

Project code is [MIT](https://github.com/kadirb4rut/voxadub/blob/main/LICENSE); models, dependencies and media have separate terms. See [third-party notices](https://github.com/kadirb4rut/voxadub/blob/main/THIRD_PARTY_NOTICES.md). Obtain rights and voice consent for media you process or distribute.
