# Release readiness

The repository currently ships as source plus pinned setup scripts, not as a standalone executable. That is intentional: VoxCPM2, WhisperX, PyTorch, and optional LatentSync each bring multi-gigabyte model/runtime requirements that make a one-file bundle fragile and difficult to update safely.

## Current release path

- Use the `demo-videos` release for public sample media.
- Use `./install.sh` or `install.ps1` for a reproducible source installation.
- Run `scripts/check_setup.py` before a first dub.
- Keep model weights, generated media, transcripts, and virtual environments out of git.

## Future packaged launchers

A later release can provide platform-specific launchers that create the same isolated environment and download models on first run. Any packaging work should keep model downloads external, preserve third-party notices, and be validated on each target OS/GPU combination before claiming standalone support. PyInstaller-style bundling of the full AI stack is not currently treated as release-ready.
