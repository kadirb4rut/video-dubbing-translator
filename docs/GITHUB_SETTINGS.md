# VoxaDub GitHub settings and repository migration

## Rename status

The existing public repository was renamed in place through GitHub Settings → General on 2026-11-03:

- Previous repository name: video-dubbing-translator
- Current canonical URL: https://github.com/kadirb4rut/voxadub

GitHub reported that the requested name was available before the rename. This is the same repository; no repository was created or transferred. The main branch, commit history, issues, stars, forks, releases, and tags remain with it. GitHub Actions passed on the merge commit immediately before the rename.

The README, changelog, release documentation, and security contact link are being updated to the canonical URL in a documentation-only pull request. Keep that pull request unmerged until maintainer approval.

## Visual identity

The README hero uses the original approved artwork at [assets/branding/voxadub-banner.png](../assets/branding/voxadub-banner.png). That tracked PNG remains unchanged.

GitHub rejected the 1.62 MB PNG for its Social Preview because the upload limit is 1 MB. The Social Preview now uses a 1280×640 JPEG encoding of the same banner artwork, optimized to about 202 KB. The composition and text remain visually clear; the original PNG was not replaced or edited.

## About metadata

The repository About description is:

> VoxaDub — Local-first AI video and live broadcast dubbing with Hy-MT2 translation and speaker-aware VoxCPM2 voices. Live Dubbing is experimental.

Topics remain: ai-dubbing, ai-video, latentsync, local-ai, open-source, python, speech-translation, video-dubbing, video-localization, video-translation, voice-cloning, voxcpm2, whisper, whisperx.

## Local clone

The existing VoxaDub worktree now uses the canonical origin:

~~~sh
git remote set-url origin https://github.com/kadirb4rut/voxadub.git
git fetch origin
~~~

A fresh clone creates a directory named voxadub:

~~~sh
git clone https://github.com/kadirb4rut/voxadub.git
cd voxadub
./install.sh
./start_gui.sh
~~~

On Windows, run install.ps1 and start_gui.bat from PowerShell after changing into the voxadub directory.

## Compatibility

The repository rename changes its GitHub URL only. Existing Modal application/function names, Colab runtime directories and paths, internal Python packages, configuration keys, and public commands remain unchanged to preserve existing deployments and installations.

Repository Settings showed no configured webhooks and no installed GitHub Apps. GitHub Actions continues to use the repository’s workflow files under the renamed repository. Any external service configured outside repository Settings should be checked separately if it stores the old URL.
