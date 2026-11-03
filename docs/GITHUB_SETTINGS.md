# VoxaDub GitHub settings and rename checklist

Repository-level metadata is separate from the files in a pull request.

## Visual identity

Use [`assets/branding/voxadub-banner.png`](../assets/branding/voxadub-banner.png) as the README hero and, when updating repository settings, as the GitHub social preview. This keeps the same approved artwork as the project's single visual identity.

## About metadata

Recommended description:

> VoxaDub — Local-first AI video and live broadcast dubbing with Hy-MT2 translation and speaker-aware VoxCPM2 voices. Live Dubbing is experimental.

The repository About description now uses this wording. The GitHub social preview remains separate repository metadata and is not changed by the README branch.

Recommended topics:

`ai-dubbing`, `ai-video`, `latentsync`, `local-ai`, `open-source`, `python`, `speech-translation`, `video-dubbing`, `video-localization`, `video-translation`, `voice-cloning`, `voxcpm2`, `whisper`, `whisperx`

## Repository rename — after separate final approval only

Desired repository URL: `https://github.com/kadirb4rut/voxadub`.

The name is not currently present in the owner's public repository list. The GitHub API returns 404 for that URL; only GitHub's rename action can confirm that it is claimable at the time of the approved rename.

Until then, keep active links, badges, installation commands, and remotes on `kadirb4rut/video-dubbing-translator`. Do not publish `voxadub` repository URLs before the rename.

After explicit approval:

1. Rename the repository through **Settings → General** and confirm the new canonical URL.
2. Update the README CI badge, release/demo/media links, and `git clone`/`cd` commands; update `.github/ISSUE_TEMPLATE/config.yml` and other active links that target the old repository path.
3. Confirm the About description matches the wording above, set the social preview to the VoxaDub banner, and verify the README and links on the renamed repository.
4. Update existing local clones with `git remote set-url origin https://github.com/kadirb4rut/voxadub.git` and verify fetch/push access.
5. Review GitHub Actions, issue/security forms, webhooks, GitHub Apps, and any external integrations that may use the old repository path. Verify a fresh clone and CI run.

Keep deployed Modal app/function names and Colab runtime paths stable during the repository rename. They are operational identifiers that are independent of the GitHub URL; migrate them only as a separate, verified compatibility change.
