# Discussion draft: Speaker-aware offline video dubbing with VoxCPM2 and Hy-MT2

**Local draft, reconciled 2026-11-01 — do not post publicly without explicit approval.**

We have prepared the next source release of Video Dubbing Translator around an offline batch workflow that uses VoxCPM2 for speaker-conditioned Turkish speech and Hy-MT2 for local translation.

The batch path extracts and separates dialogue, transcribes and aligns speech, optionally runs pyannote Community-1 locally, reconciles turns to stable speaker identities, selects a clean reference clip and its exact transcript for each speaker, translates with a locally selected Hy-MT2 checkpoint, and synthesizes each group with VoxCPM2. A duration-aware controller compares actual generated duration with the preferred active-speech window, asks Hy-MT2 for at most one bounded shortening or expansion rewrite when appropriate, and keeps conservative tempo correction as a final step. The output report records signal, timing, speech-recovery, reference-similarity, retries, and warnings.

In a saved 72-second two-speaker development run, the translation/synthesis/reconstruction path produced 11 timing groups, two speaker-specific references, and a 72.367-second output. All 11 groups passed the recorded audio QA gate, one retry was used, and six groups retained soft warnings. The run used Hy-MT2 1.8B and native VoxCPM2 on a Colab G4 RTX PRO 6000 Blackwell with CUDA 13.0 and PyTorch 2.11.0+cu130. It reused a previously computed Community-1 diarization result and an existing WhisperX-aligned transcript, so it does not represent a fresh full transcription-to-dub pass.

For one group, target duration was 5.162 seconds; initial speech was 7.20 seconds; after a bounded rewrite the final speech was 4.80 seconds. Absolute duration error fell 82.2%. This example demonstrates the timing controller, not a broad translation-quality score. ASR WER/CER compare generated Turkish audio with the selected Turkish text; they do not prove that the translation preserves the English source meaning. The reported speaker similarity is a lightweight MFCC-statistics proxy.

The default offline workflow does not require a translation API after model and alignment assets have been downloaded. Hy-MT2 weights, VoxCPM2 weights, Community-1, and language-specific ASR/alignment assets remain separate user downloads. Community-1 is gated and carries its own access conditions and attribution requirements. Our G4 is development hardware only.

The current GUI also offers full offline processing on selected Colab or Modal compute. A separate November 1 NASA English-to-Turkish Colab run completed all source stages and returned a verified full MP4; a short synthetic Modal run verified full remote processing and explicit GPU disconnection. These results are distinct from the speaker-aware 72-second evidence above. Connected cloud resources remain open until Disconnect GPU. See the current release readiness and provider E2E records for coverage and connection-recovery limits.

Live Dubbing remains experimental. We do not claim universal p95 latency, sustained livestream acceptance, complete handling of overlapping speech, or broad CUDA/platform support. We are sharing the bounded evidence and gaps for technical feedback, not presenting it as production certification.

The source sample's redistribution rights are not documented in the repository, so this Discussion draft intentionally contains no media links or dialogue excerpts. See the release's E2E and hardware records for the measurement definitions.

Feedback requested: VoxCPM2 maintainers' guidance on reproducible speaker-reference conditioning, duration-safe translation refinement, and QA metrics that distinguish faithful speech realization from source-to-target semantic accuracy.
