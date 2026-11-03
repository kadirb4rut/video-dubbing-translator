# CUDA and hardware validation matrix

Only environments with saved measurements are listed. Missing VRAM measurements are not estimated from a product specification.

| Environment | GPU / memory | CUDA / PyTorch | Workflow and measured time | Peak VRAM | Result |
| --- | --- | --- | --- | --- | --- |
| Colab G4 development VM | NVIDIA RTX PRO 6000 Blackwell Server Edition; VRAM capacity not recorded | CUDA 13.0 / PyTorch 2.11.0+cu130 | Hy-MT2 1.8B + native VoxCPM2 dubbing/reconstruction, 72.13 s sample; warm path 104.50 s, TTS generation 44.71 s | Not recorded | PASS for this bounded run; not a general compatibility guarantee |
| macOS 15.5 Apple Silicon browser host + Colab G4 | Apple Silicon hosted Chromium; all Live Dubbing inference ran remotely on NVIDIA RTX PRO 6000 Blackwell | Mac CUDA not used; remote G4 used CUDA 13.0 / PyTorch 2.11.0+cu130 | Bundled browser launcher, extension tab capture, authenticated tunnel, Turkish speech playback, Finish/drain and source audio restoration | Not measured on Mac | PASS for this bounded launcher/tunnel E2E; does not validate local Mac GPU inference |
| November 1 managed Colab G4, macOS GUI client | NVIDIA RTX PRO 6000 Blackwell; capacity/peak not recorded in this test summary | Isolated Python 3.12.3 cloud environment; exact CUDA/PyTorch versions not captured in the retained summary | Fresh full NASA English WebM → Turkish job, 228.744 s source / 228.979 s output; decode/range/download checks | Not recorded | PASS for one full remote batch job with Hy-MT2 1.8B; temporary cache, Community-1 disabled; no persistent Drive/other-model guarantee |
| November 1 private Modal full-pipeline app | App requests A10G/L4; exact allocated device is not identified in the saved summary | Exact versions not captured in the retained summary | 7.084 s synthetic-video return; real GUI Disconnect stopped app/containers | Not recorded | PASS for bounded offline serving/lifetime; not Modal Live, broad GPU certification or semantic acceptance |
| Apple M1 MacBook Pro, 8 GB unified memory | Apple M1 GPU available; the recorded full pipeline chose CPU/bfloat16 for VoxCPM2 | CUDA not applicable; current checked main environment is PyTorch 2.5.1 | Historical 72 s full local pipeline; 4,528.09 s wall time. A separate Hy-MT2 1.8B MPS smoke took 42.03 s, but was not an end-to-end MPS dub. | Not recorded | PASS for historical CPU workflow; MPS evidence is component-only |
| Windows consumer installation | Not tested | Not tested | No clean installation or E2E run | Not recorded | NOT TESTED |
| Other NVIDIA GPUs, CUDA releases, and Linux consumer distributions | Not tested in this release pass | Not tested | No new matrix run started | Not recorded | NOT TESTED |

The G4 is development hardware only. Users do not need Colab. This release does not claim a minimum CUDA VRAM requirement, peak VRAM budget, or support for GPUs that were not exercised. LatentSync remains a separate optional CUDA workflow and was not tested here.


The November 1 provider details are in [GUI compute E2E](GUI_COMPUTE_E2E_20261101.md)
and [Live cloud verification](LIVE_CLOUD_VERIFICATION_20261101.md). Frontend
screenshots and compact-size checks do not expand this GPU support matrix.
