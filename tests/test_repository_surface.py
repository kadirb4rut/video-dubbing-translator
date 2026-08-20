import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositorySurfaceTests(unittest.TestCase):
    def test_readme_keeps_truthful_first_screen_and_local_links(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Local-first is not fully offline", readme)
        self.assertIn("# Demo", readme)
        self.assertIn("# Quick Start", readme)
        self.assertIn("scripts/create_demo_comparison.sh", readme)
        self.assertIn("docs/assets/social-preview.svg", (ROOT / "docs/GITHUB_SETTINGS.md").read_text(encoding="utf-8"))

        local_links = re.findall(r"\]\(([^)#]+)\)", readme)
        for link in local_links:
            if link.startswith(("http://", "https://", "#")):
                continue
            self.assertTrue((ROOT / link).exists(), f"README link is missing: {link}")

    def test_bootstrap_assets_exist_and_social_preview_dimensions_are_declared(self):
        self.assertTrue((ROOT / "install.sh").is_file())
        self.assertTrue((ROOT / "install.ps1").is_file())
        self.assertTrue((ROOT / "scripts/create_demo_comparison.sh").is_file())
        social_preview = (ROOT / "docs/assets/social-preview.svg").read_text(encoding="utf-8")
        self.assertIn('width="1280"', social_preview)
        self.assertIn('height="640"', social_preview)


if __name__ == "__main__":
    unittest.main()
