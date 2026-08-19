import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import voxcpm_runtime


class VoxCPMRuntimeTests(unittest.TestCase):
    def test_pinned_manifest_contains_exact_major_artifact_sizes(self):
        self.assertEqual(voxcpm_runtime.VOXCPM_OUTPUT_SAMPLE_RATE, 48_000)
        self.assertEqual(voxcpm_runtime.VOXCPM_REFERENCE_SAMPLE_RATE, 16_000)
        self.assertEqual(
            set(voxcpm_runtime.VOXCPM_EXPECTED_SIZES),
            {"audiovae.pth", "model.safetensors"},
        )
        self.assertTrue(
            set(voxcpm_runtime.VOXCPM_EXPECTED_SIZES).issubset(
                voxcpm_runtime.VOXCPM_REQUIRED_FILES
            )
        )

    def test_device_environment_override_does_not_require_torch(self):
        with patch.dict(os.environ, {"VOXCPM_DEVICE": "cpu"}, clear=False):
            self.assertEqual(voxcpm_runtime.default_runtime_device(), "cpu")

    def test_model_ready_validates_required_files_and_exact_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory)
            for filename in voxcpm_runtime.VOXCPM_REQUIRED_FILES:
                path = model_path / filename
                path.touch()
                expected_size = voxcpm_runtime.VOXCPM_EXPECTED_SIZES.get(filename)
                if expected_size is not None:
                    with path.open("r+b") as handle:
                        handle.truncate(expected_size)

            with patch.object(voxcpm_runtime, "resolve_model_path", return_value=model_path):
                self.assertEqual(voxcpm_runtime.model_ready(), (True, model_path))
                with (model_path / "audiovae.pth").open("r+b") as handle:
                    handle.truncate(1)
                self.assertEqual(voxcpm_runtime.model_ready(), (False, None))


if __name__ == "__main__":
    unittest.main()
