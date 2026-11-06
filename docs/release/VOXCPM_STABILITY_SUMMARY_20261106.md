# VoxaDub stability test summary for the VoxCPM community tester

Against baseline commit `392dda239e15352f40197391ac8f45665881398b`, this independent change adds media validation and safer transcript/reference handling, atomic GUI uploads, authorized loopback-only output retrieval, stale-session recovery, and sanitized private diagnostics. The Community-1 readiness check now verifies both PLDA files required by VBx.

The full nine-case matrix was run through VoxaDub's production pipeline on a Colab NVIDIA RTX PRO 6000 Blackwell Server Edition (97,887 MiB). The app, Hy-MT2, and Community-1 runtimes used Python 3.12.3 with CUDA operations enabled. Six valid synthetic-media inputs produced H.264/AAC outputs that passed full decode; no timing-QA hard failures occurred. The no-audio, audio-only, and 0.6-second cases rejected early with actionable messages. Processing ranged from 38.21 to 109.67 seconds; observed VRAM peaks ranged from 11,179 to 11,603 MiB (sampled `nvidia-smi`, not allocator maximum).

The unresolved quality point is unconstrained two-speaker diarization: Community-1 returned three labels for two synthetic speakers. With the known count set to `--num-speakers 2`, it returned two labels for all twelve groups. This is not evidence of acoustic speaker similarity or human-rated voice quality; the over-splitting limitation remains documented.

The independent tester's patch and sweep script were not available in the searched local attachments, so the fixes were implemented independently. These results do not claim VoxCPM endorsement or an apples-to-apples speed comparison with RTX 4090 tests. Full details and sanitized evidence are in [`STABILITY_VERIFICATION_20261106.md`](STABILITY_VERIFICATION_20261106.md).
