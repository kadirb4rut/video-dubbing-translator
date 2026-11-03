import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositorySurfaceTests(unittest.TestCase):
    def test_readme_keeps_truthful_first_screen_and_local_links(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("fully local", readme.lower())
        self.assertIn("## Offline Dubbing — Multilingual Demo", readme)
        self.assertIn("## Live Dubbing — YouTube Live Broadcast Demo", readme)
        self.assertIn("# Quick Start", readme)
        self.assertIn("scripts/create_demo_comparison.sh", readme)
        self.assertIn("timing-group", readme)
        self.assertIn("0.85×–1.25×", readme)
        self.assertIn("docs/assets/social-preview.svg", (ROOT / "docs/GITHUB_SETTINGS.md").read_text(encoding="utf-8"))

        local_links = re.findall(r"\]\(([^)#]+)\)", readme)
        for link in local_links:
            if link.startswith(("http://", "https://", "#")):
                continue
            self.assertTrue((ROOT / link).exists(), f"README link is missing: {link}")

    def test_google_translate_is_absent_from_normal_code_path(self):
        source = (ROOT / "Video_Translator.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("GoogleTranslator", source)
        self.assertNotIn("deep-translator", requirements)

    def test_browser_gui_exposes_local_model_and_speaker_controls(self):
        gui = (ROOT / "web_gui.py").read_text(encoding="utf-8")
        for control in (
            'id="translationModel"',
            'id="translationDevice"',
            'id="diarization"',
            'id="numSpeakers"',
            'id="speakerBounds"',
            'id="glossary"',
            'id="createPairingButton"',
            'id="copyPairingButton"',
            'create a one-use code here and enter it in the extension panel',
            "/api/download-hymt2",
            "/api/setup-diarization",
        ):
            self.assertIn(control, gui)
        extension_client = (ROOT / "browser_extension/live_dubbing/client.js").read_text(encoding="utf-8")
        self.assertIn("$('pairingDetails').hidden = true;", extension_client)
        extension_page = (ROOT / "browser_extension/live_dubbing/player.html").read_text(encoding="utf-8")
        self.assertIn("enter it above. Codes expire after 120 seconds", extension_page)

    def test_bootstrap_assets_exist_and_social_preview_dimensions_are_declared(self):
        self.assertTrue((ROOT / "install.sh").is_file())
        self.assertTrue((ROOT / "install.ps1").is_file())
        self.assertTrue((ROOT / "scripts/create_demo_comparison.sh").is_file())
        social_preview = (ROOT / "docs/assets/social-preview.svg").read_text(encoding="utf-8")
        self.assertIn('width="1280"', social_preview)
        self.assertIn('height="640"', social_preview)


if __name__ == "__main__":
    unittest.main()
