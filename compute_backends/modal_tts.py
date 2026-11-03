"""Optional Modal function adapter for offline VoxCPM2 speech generation."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


JOB_IDLE_WINDOW_SECONDS = 60
RELEASE_IDLE_WINDOW_SECONDS = 2


def create_modal_synthesizer(
    *,
    app_name: str = "video-dubbing-translator",
    function_name: str = "synthesize_wav",
    modal_module=None,
) -> Callable:
    """Return a callback compatible with ``generate_dubbed_speech``.

    The repository's deployed Modal function is invoked through Modal's
    authenticated SDK. No public HTTP endpoint or saved token is required.
    Transcription, translation, separation, QA and video reconstruction stay
    on the machine running the batch pipeline.
    """
    if modal_module is None:
        try:
            import modal as modal_module
        except ImportError as exc:
            raise RuntimeError(
                "Modal TTS needs the optional Modal client. Install it with "
                "`python -m pip install -r requirements-modal.txt`, run `modal setup`, "
                "and deploy `modal_app.py` once with `modal deploy modal_app.py`."
            ) from exc

    try:
        remote_function = modal_module.Function.from_name(app_name, function_name)
        hydrate = getattr(remote_function, "hydrate", None)
        if callable(hydrate):
            hydrate()
    except Exception as exc:
        raise RuntimeError(
            f"Could not find deployed Modal function {app_name}/{function_name}. "
            "Run `modal setup` and `modal deploy modal_app.py` first."
        ) from exc

    # Modal 1.5 exposes update_autoscaler(); newer SDKs expose
    # autoscaler.update(). Keep both surfaces supported by requirements-modal.
    autoscaler_update = getattr(remote_function, "update_autoscaler", None)
    if not callable(autoscaler_update):
        autoscaler = getattr(remote_function, "autoscaler", None)
        autoscaler_update = getattr(autoscaler, "update", None)
    if callable(autoscaler_update):
        try:
            # Keep the function warm between timing groups and QA retries in
            # this job. A prior completed job may have set the short release
            # window, so restore the normal in-job reuse window on startup.
            autoscaler_update(scaledown_window=JOB_IDLE_WINDOW_SECONDS)
        except Exception as exc:
            print(
                f"Could not restore Modal's in-job warm window: {exc}. "
                "The deployed function's configured scale-down window remains active.",
                flush=True,
            )

    def synthesize(text, selected_reference, output_path, *, seed=42, style=None, **_kwargs):
        reference_path = Path(selected_reference.wav_path).expanduser().resolve()
        if not reference_path.is_file():
            raise FileNotFoundError(f"Modal voice reference audio is missing: {reference_path}")
        try:
            wav_bytes = remote_function.remote(
                text,
                reference_path.read_bytes(),
                getattr(selected_reference, "transcript", "") or "",
                style or "",
                int(seed),
            )
        except Exception as exc:
            raise RuntimeError(f"Modal VoxCPM2 request failed: {exc}") from exc
        if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes.startswith(b"RIFF"):
            raise RuntimeError("Modal VoxCPM2 returned an invalid WAV response.")
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(wav_bytes)
        return destination

    closed = False

    def close_modal_compute() -> None:
        """Ask Modal to release idle GPU containers after this local job."""
        nonlocal closed
        if closed:
            return
        closed = True
        if not callable(autoscaler_update):
            print(
                "Modal will scale idle GPU containers to zero using the deployed function's "
                "configured idle window.",
                flush=True,
            )
            return
        try:
            settings = autoscaler_update(scaledown_window=RELEASE_IDLE_WINDOW_SECONDS)
            confirmed_window = getattr(settings, "scaledown_window", None)
            confirmation = (
                f"Modal confirmed a {confirmed_window}-second idle window."
                if confirmed_window is not None
                else "Modal accepted the shorter idle window."
            )
            print(
                "Modal GPU work is finished. " + confirmation + " Its autoscaler will release "
                "idle GPU containers; provider shutdown may take longer than the configured window.",
                flush=True,
            )
        except Exception as exc:
            print(
                f"Could not shorten Modal's idle GPU window: {exc}. "
                "Modal will still scale idle containers to zero using the deployed function's "
                "configured idle window.",
                flush=True,
            )

    # The pipeline calls this only after all dubbing synthesis and QA work is
    # done. Keeping a callable attribute avoids changing the TTS callback API.
    synthesize.close = close_modal_compute

    return synthesize
