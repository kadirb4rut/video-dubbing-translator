# Contributing

Thanks for helping improve Video Dubbing Translator. Focused fixes, documentation improvements, platform notes, and reproducible bug reports are welcome.

## Before opening an issue

1. Use Python 3.10 in a fresh virtual environment.
2. Run `python scripts/check_setup.py`.
3. Test with a short, non-sensitive video you have permission to process.
4. Search existing issues for the same failure.

Do not attach private source media, transcripts, cloned-voice samples, credentials, or model weights to an issue.

## Development setup

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts/check_setup.py --skip-imports
```

Model setup is optional for documentation and static checks. Full inference requires the pinned VoxCPM2 snapshot; download it with `python scripts/setup_models.py` and never commit its files or caches.

## Pull requests

- Keep changes narrowly scoped; this project is not seeking a pipeline rewrite.
- Preserve working CLI and browser-GUI behavior.
- Run `python -m compileall -q .` and the relevant safe script checks.
- Explain what was actually tested, what was only inspected, and what could not be run.
- Never commit generated media, transcripts, caches, environments, or model checkpoints.
- If adding a dependency, model, dataset, copied snippet, or external service, update `THIRD_PARTY_NOTICES.md` with its exact source, revision, license, and use restrictions.
- New public claims must match the code. In particular, do not describe the current translation path as offline.

## Responsible voice use

Contributions must not encourage impersonation, fraud, harassment, non-consensual voice cloning, or deceptive media. Test only with media and voices you have permission to use, and disclose synthetic speech where appropriate.
