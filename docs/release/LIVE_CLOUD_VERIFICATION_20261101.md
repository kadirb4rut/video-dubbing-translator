# Live cloud verification — 2026-11-01

On macOS, the GUI cloud setup prepared a new Colab CLI G4 runtime using
temporary storage. The G4 reported NVIDIA RTX PRO 6000 Blackwell. The product
launcher opened local Chromium and a loopback-only authenticated SSH tunnel to
the existing Live service on G4. The browser extension was paired with the
service and captured the user-authorized Sky News YouTube live tab.

The panel displayed an English source phrase and its Turkish translation,
followed by `Playing validated speech`. The displayed video delay and buffer
were approximately 16 seconds; no independent lip-sync measurement was made.
Finish reached `Finished` after the confirmed queue drained; an unconfirmed
tail was explicitly left untranslated. Stop returned the YouTube tab's sound
indicator and the service reported no active session. Community-1 weights were
not installed: the existing temporary voice fallback was used.

The initial managed source archive omitted the browser-extension files used by
the remote service's index page. The archive allowlist was fixed and a new
archive uploaded to the test runtime before the browser test. Live preparation
also now prepares the live service's Hy-MT2 1.8B model when a different offline
model is selected. Direct Modal live preparation requests are rejected because
the Modal adapter currently implements offline jobs only; they previously could
prepare an offline app while being passed `--live`.

The current application policy leaves G4 running until the user selects
Disconnect GPU; this dated test record does not assert the runtime is still
allocated when read later. Its temporary model cache disappears when stopped. Cloud providers may
independently terminate runtimes. Modal Live remains unimplemented and was not
tested; the earlier Modal offline test is not evidence of Live support.


## Subsequent GUI work and scope

The main panel now contains independent Live/Offline provider selectors and
compact responsive styling, with action feedback described in
[UI verification](UI_UX_VERIFICATION_20261101.md). That work checked browser
layout, control behavior and mocked job status; it did not repeat this actual
Sky News E2E or remeasure A/V offset, voice identity, latency or semantic quality.
A GUI-server restart can lose the remembered Colab readiness state while a
previous runtime is still active; see [current readiness](../RELEASE_READINESS.md).
