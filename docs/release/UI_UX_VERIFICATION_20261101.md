# Web GUI UI/UX verification — 2026-11-01

This record covers `web_gui.py` presentation and action feedback. It does not
certify a fresh offline or Live model run. Current overall status is maintained
in [project/release readiness](../RELEASE_READINESS.md).

## Implemented presentation

- One main panel retains independent Live and Offline provider selections.
  Desktop layout groups task controls on the left and output/logs on the right;
  a narrow viewport stacks the layout.
- Lighter blue theme, consistent labels and spacing, visible keyboard focus,
  clear primary actions, and a useful empty output state.
- Model, speaker, glossary and lip-sync controls remain available under
  **Advanced model, speaker and lip-sync settings**.
- Action buttons use a shared waiting indicator and sticky activity message.
  Cloud preparation states and job status come from the existing server.
  Request submission is distinct from completion. Error messages remain in the
  page; status-polling failure is visible. Reduced-motion preference disables
  spinner rotation. The UI does not invent percentages, ETAs or unreported
  ASR/translation/TTS stage progress.
- The Live extension's capture, playback, Finish/drain and Stop UI was not
  redesigned in this pass. Modal Live remains disabled.

## Final compact sizing

| Element | Before density pass | Current desktop |
| --- | ---: | ---: |
| Body text | 15 px | 14 px |
| Main heading | 25 px | 22 px |
| Main panel heading | 19 px | 16 px |
| Input/button minimum height | 44 px | 36 px |
| Panel padding | 24 px | 16 px |
| Main content maximum width | 1520 px | 1280 px |
| Process log height | 280 px | 220 px |
| Measured Live panel height at 1440×722 | about 336 px | about 272 px |

Narrow-screen input/buttons use at least 40 px; coarse-pointer controls retain
44 px targets. The empty video area is bounded to 180–260 px height, with full
available width. Chrome zoom was not changed; no CSS zoom or scale workaround
was used.

## Verification and limits

- macOS Chrome, user's normal 100% view: 1440×722 CSS pixels, device-pixel ratio
  2 and visual viewport scale 1. Final controls measured 36 px; body text 14 px.
- A temporary 390×844 viewport showed no horizontal overflow. The viewport
  override was reset after inspection.
- Local↔Colab changed the Live connection-button visibility correctly; advanced
  model controls expanded; visible controls did not extend outside the viewport.
- Missing-file upload showed **Choose a video first.** in the activity area.
- A temporary mock server exercised **Setup Runtime** through a busy preparing
  state to **Done**. No real model installation/cloud work was invoked by this
  feedback test. Not every account/provider action was individually replayed.
- Final post-refresh console observation contained no new warning/error. Earlier
  development-server restarts generated stale-token/fetch errors in old pages;
  reloading obtained the new server token. Polling now reports connection issues.
- Post-redesign targeted regression: **7 passed in 2.76 s** across
  `tests/test_cloud_gui.py` and `tests/test_live_browser_launcher.py`. The project
  environment lacked pytest, so the already-installed pytest package was used
  on the project Python with unrelated plugin autoload disabled; no packages
  were installed. The saved full-suite result is separately dated in the
  [provider test record](GUI_COMPUTE_E2E_20261101.md).
- After the final CSS-only density changes: Python syntax passed; comparison
  confirmed unchanged backend AST and page JavaScript. No extra full suite or
  real GPU E2E was run for those sizing edits.

The design was inspected using screenshots in Chrome and the in-app preview.
Temporary mock code, test settings and screenshots were removed; historical
source/media test records were preserved. The separate development preview port
18765 is not a new public product entry point. Normal startup remains
`./start_gui.sh` on 127.0.0.1:8765. An old running Python GUI must be restarted to
serve edited page code; its connection-state recovery limitation is documented
in [compute providers](../COMPUTE_PROVIDERS.md#gui-restart-and-connection-state).

Design references: [Carbon compact input guidance](https://www.carbondesignsystem.com/building-blocks/core/components/text-input/guidelines),
[Atlassian spacing](https://atlassian.design/foundations/spacing), and
[W3C form notifications](https://www.w3.org/WAI/tutorials/forms/notifications/).
No commit, push, PR or GitHub Release was created.
