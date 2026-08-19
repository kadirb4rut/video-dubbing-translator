import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from voxcpm_runtime import (  # noqa: E402
    VOXCPM_MODEL_ID,
    VOXCPM_MODEL_REVISION,
    VOXCPM_EXPECTED_SIZES,
    VOXCPM_REQUIRED_FILES,
    model_ready,
    resolve_model_path,
)


def run(command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=BASE_DIR, check=True)


def main():
    run([sys.executable, BASE_DIR / "scripts" / "download_model.py"])

    ready, model_path = model_ready()
    if ready:
        print(f"VoxCPM2 model already exists and passed verification: {model_path}")
    else:
        free_gb = shutil.disk_usage(BASE_DIR).free / (1024**3)
        if free_gb < 6:
            raise RuntimeError(
                f"Only {free_gb:.1f} GB of free disk space remains. VoxCPM2 needs approximately "
                "5 GB for its pinned model snapshot; free at least 6 GB and retry."
            )

        print(
            f"Downloading VoxCPM2 model files from {VOXCPM_MODEL_ID} at "
            f"revision {VOXCPM_MODEL_REVISION}...",
            flush=True,
        )
        model_path = resolve_model_path(local_files_only=False)

    ready, verified_path = model_ready()
    if not ready or verified_path != model_path:
        issues = []
        for name in VOXCPM_REQUIRED_FILES:
            path = model_path / name
            if not path.is_file():
                issues.append(f"{name} is missing")
            elif name in VOXCPM_EXPECTED_SIZES and path.stat().st_size != VOXCPM_EXPECTED_SIZES[name]:
                issues.append(
                    f"{name} has {path.stat().st_size} bytes; expected {VOXCPM_EXPECTED_SIZES[name]}"
                )
        raise RuntimeError(f"VoxCPM2 setup is incomplete: {'; '.join(issues)}")

    size_gb = sum((model_path / name).stat().st_size for name in VOXCPM_REQUIRED_FILES) / (1024**3)
    print(f"VoxCPM2 model ready: {model_path} ({size_gb:.2f} GB)")
    print("VoxCPM2 code and model weights are Apache-2.0 and permit commercial use.")


if __name__ == "__main__":
    main()
