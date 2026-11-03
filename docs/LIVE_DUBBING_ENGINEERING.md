# Adaptive latency live dubbing — engineering record

Current status index, reconciled 2026-11-01: [project/release readiness](RELEASE_READINESS.md), [managed Colab Live verification](release/LIVE_CLOUD_VERIFICATION_20261101.md), and [UI verification](release/UI_UX_VERIFICATION_20261101.md).

The sections below are a **historical engineering record**, primarily October 25–28. Their pending gates, test counts, local-only placement instructions and old shutdown checklist describe those checkpoints; they are not the latest product/provider policy. Later bounded real YouTube capture/tunnel tests passed, but universal quality, latency, speaker identity and production acceptance remain open. The current GUI offers optional Colab inference and retains connected cloud resources until explicit Disconnect; Modal Live is not implemented. Earlier failures and experimental model results are preserved for audit.
Approved offline artifact SHA-256:
`9b296ece3680d2739ee0616710f303ed7040c2ffff341c3ab4ed0307fe2053e7`.
Do not overwrite that artifact or change offline defaults.

## Requirements and acceptance evidence

The original specification was supplied in a private user attachment, which is not included in this repository.
All gates remain open until actual evidence is attached here. Unit tests and
transport simulations never count as real model/browser/YouTube gates.

| Gate | Required evidence | Current status |
|---|---|---|
| A | Wall-clock audio arrival, real incremental ASR/translation/native streaming TTS, semantic review | Real 12.4-second confirmed-prefix revalidation passed; vocabulary/semantic coverage remains limited |
| B | Same real source in Quality/Balanced/Low Latency, decision reasons and measured latency | Matched real 12.4-second runs: 3/4/4; later 16.76-second runs: 4/4/4 execution-only. Quality terminal-word defect subsequently fixed and revalidated with four real outputs; general semantic/naturalness acceptance remains open |
| C | Real multiple speakers, distant turns, reference integrity, interjections, overlap fallback | Identity adapter: A/B/A/B matched; full playback continuity remains open |
| D | Actual delayed video presentation + generated audio, measured offset/drift and recovery | Three actual dubbed phrases played with frame callbacks; later MSE pruning failure, not passed |
| E regular | Permitted real YouTube session with actual models, controls, synchronization | Real native Turkish speech and delayed video played; pause/resume observed; acceptance still open. User completed sign-in; latest real session generated seven/played six phrases, then stopped on source uncertainty |
| E live | Permitted actual arriving YouTube live content; no full download/preloading | Pending |
| F | Extended real continuous playback, queue/memory/drift/duplicates/language/stall audit | Pending |

Every performance report must separate model load, model first chunk, QA,
intentional buffering, playback scheduling and actual presentation. Report
p50/p95/max from observed samples; missing observations stay null.

## Current architecture audit

`engine/streaming.py` is a legacy phrase-level primitive. It runs ASR only after
energy VAD endpoint, does not actually use LocalAgreement during arrival, and
has no video buffer/player. Its MFCC tracker is not reliable speaker identity
evidence. `scripts/live_file_smoke.py` feeds data faster than real time, uses a
fixed reference despite tracker IDs, and calls Modal. Neither proves the new
goal. Keep their existing API for compatibility; implement the new optional
feature in `live_dubbing/` and a separate browser player.

Reuse the repaired native VoxCPM runtime (10 steps, no spoken style prose,
short-target continuation guard), the isolated persistent Hy-MT2 worker, language
router, QA, relevant SQLite memory, and existing explicit model setup.
No model is downloaded on import or page load. Consumer traffic stays on
loopback. Colab is a development test runner only, never a runtime dependency.

## Browser/YouTube constraint review (2026-10-25)

Primary sources inspected:

- [Chrome user-gesture tab capture and offscreen/visible-document patterns](https://developer.chrome.com/docs/extensions/how-to/web-platform/screen-capture).
- [Chrome tabCapture API](https://developer.chrome.com/docs/extensions/reference/api/tabCapture).
- [YouTube API Developer Policies, III.G.1](https://developers.google.com/youtube/terms/developer-policies).
- [YouTube Terms, Permissions and Restrictions](https://www.youtube.com/static?template=terms).

Chrome supports explicit action-triggered capture of the selected tab. That
grants browser capture capability, not content/platform permission.
YouTube API policy III.G.1.7 prohibits isolating/modifying audiovisual components
and applying alternative audio tracks. III.G.1.6/9 also restrict player
modification/background players. Therefore an IFrame API player with a replaced
audio track is not an eligible architecture. Do not use stream extraction,
`yt-dlp`, protected URLs, ad removal, player internals or DRM workarounds.

A generic user-consented full-tab capture with a separate delayed local preview
does not use YouTube APIs. The API alternate-track prohibition must not be
misrepresented as a blanket ban on every browser-capture route. It still needs
an applicable permission basis under the YouTube Terms; browser permission is
not a legal certification. The experimental selected-tab route requires an
explicit user authorization declaration, captures the rendered tab including
its normal player/advertising, and never extracts streams, alters the original
player, bypasses DRM or manipulates advertising. It does not claim to override
platform/content restrictions. Protected/unavailable sources remain unsupported.
General YouTube compliance/rights are not certified by a successful technical
test or a checkbox. Any distribution requires separate policy/legal review;
this task remains private and unpublished.

User nominated `https://www.youtube.com/watch?v=DFYaNjzI1aI` as a test candidate.
The search title is “LIVE: Talk 24/7”; the actual live player was subsequently verified. The user supplied a regular-video candidate as well. The later development tests captured only the explicitly selected Colab browser tab and sent its PCM to the same VM's loopback inference service.

Update: a real Chrome session confirmed the first URL is “LIVE: Talk 24/7”
with a visible live viewer count. User also nominated the regular-video candidate
`https://www.youtube.com/watch?v=AuZoDsNmG_s`. On 2026-10-25 the user clarified
that the development browser/capture and models should all execute on Colab G4,
not on this Mac. Do not require a separate local GPU host for these development
tests. Keep consumer inference local and avoid forwarding a user's Mac browser
capture to a cloud inference service. Capture on the development VM itself is a
different, explicitly requested test setup. Platform-permission questions remain
separate from GPU placement.

## Selected implementation boundaries

1. Lazy optional loopback live service; explicit Start loads models. Stop closes
   capture, worker tasks, translator subprocess and model references.
2. MV3 extension using activeTab + tabCapture, a visible extension player, no
   history/tabs-wide/content-script permissions. Same bundled player is served
   locally for browser `getDisplayMedia` capture of permitted sources.
3. Capture timestamps and monotonically increasing PCM sequence numbers. Reject
   duplicates, gaps, backwards clocks, malformed formats and oversized packets.
4. Stateful Silero ONNX VAD, rolling faster-whisper with timestamped
   LocalAgreement; provisional text is never played. Explainable semantic
   endpoint decisions differ by mode, without invented confidence probabilities.
5. Embedding-based online speaker matching and immutable clean references;
   uncertain/short/overlap audio must not update a voice reference. A temporary
   model base voice is explicit, rather than borrowing another speaker's voice.
6. Persistent translation/native streaming TTS. All actual target text and PCM
   request IDs are traced. Complete generated audio is held for bounded QA
   before it becomes playable; model TTFA differs from first usable audio.
7. Bounded encoded A/V capture buffer and a controlled delayed player, original
   audio muted in translated mode. Actual playback acknowledgements drive
   offset/queue metrics. No timeline simulation is accepted as Gate D.
8. Queue limits cause explicit backpressure/error recovery, never silent dropping
   of important content. Played revisions cannot be replayed.
9. Same-origin validation, exact Host checks, secure random session credentials,
   one-use expiring extension pairing, no wildcard CORS/public bind/token URLs.

## Work sequence

- [x] Read full goal, preserve approved artifact hash, inspect existing live code.
- [x] Inspect official browser and YouTube constraints.
- [x] Implement protocol, LocalAgreement, adaptive controller and meaningful tests.
- [x] Implement real local model adapters, speaker tracking and streaming-safe QA.
- [x] Implement authenticated service, separate UI and MV3 integration.
- [ ] Perform sequential real model/capture/playback gates; retain negatives.
- [ ] Final tests, setup/CLI/GUI, secret/large-file scan, baseline hash, stop GPUs.
- [ ] Final report covering all 20 deliverables with working/limited/experimental/
      blocked/not-implemented statuses, demonstrations and machine metrics.

No commits, pushes, PRs, package/extension publication or remote GitHub mutations.

## Preserved real evidence and recovery checkpoint (2026-10-25)

The first development VM expired during an account usage-limit interruption.
Its raw VM-only WAV/JSON files are no longer available; local CLI stdout logs
remain valid evidence, with their limitations explicitly recorded in
`temp/live-validation-20261025/real-evidence-preserved.json`. Do not regenerate
completed model tests merely to recreate lost raw artifacts. A new G4 VM has
been prepared with the same pinned VoxCPM2 and Hy-MT2 weights; no model weights
were downloaded to the Mac. New small diagnostic checkpoints are saved locally
so another VM termination will not erase all evidence.

- Native source-conditioning A/B/A/B cache probe: four actual outputs recovered
  in Turkish, WER 0 for the four chosen short texts; three source-feature cache
  hits after the first miss. No non-silent chunk duplicates. These are
  intelligibility/cache checks, not a general semantic-translation certification.
- Online Community-1 embedding probe on actual conversation excerpts: distinct
  A/B identities and correct A/B/A/B distant returns. A constructed sum of two
  real speakers was flagged as overlapping. A short unknown excerpt received
  no cloned reference. This does not prove full live multi-speaker playback.
- Last complete Python suite: 113/113 passed on Colab (including optional CPU syntax diagnostics where installed). Three Node regression scripts
  passed for PCM clock continuity/final flush and Chrome play/pause promise
  cancellation and drain-audio ownership. Unit tests are not counted as real browser/model acceptance.
- Chrome 156.0.8078.47 runs sandboxed as an unprivileged Colab user. The local
  unpublished extension was installed through Chrome's UI after explicit user
  approval. Its activeTab/tabCapture/session-storage permissions do not include
  microphone, cookies, browsing history or unrelated page access.
- Stop/error disposal was observed: all recorded owned inference workers exited
  and `nvidia-smi` showed no remaining compute process after disposal.

Real failures retained and addressed in the current source:

1. Counting source age as processing age caused false backpressure. Queue age
   now starts at the actual monotonic dispatch/commit time.
2. Empty input channels during silence deleted elapsed media time. The worklet
   now emits silence for those real render quanta. Actual callback/media-clock
   differences after the fix were about 30 ms in the short observed capture.
3. Raw segmentation windows included model padding. Overlap measurements now
   mask frames outside the actual source waveform. Short-context classification
   remains limited; uncertain identity uses a clearly labeled neutral voice,
   without borrowing or updating another speaker's reference. Only dominant
   stable ASR content is translated; simultaneous speech is not separated.
4. Chrome can reject a pending `play()` when a normal buffering pause interrupts
   it. The player now keeps one pending play request and handles the expected
   AbortError without converting it into a fatal pipeline error. Other media
   permission/decoder errors remain visible. See Chrome's [official explanation](https://developer.chrome.com/blog/play-request-was-interrupted).
5. Requiring the entire growing ASR prefix to agree repeatedly lost previously
   confirmed words when an old clipped first word changed. The current code
   retains the twice-observed prefix separately from translation commitment,
   confirms only the remaining tail, and preserves timestamped repetitions.
   This follows the confirmed-buffer principle described by [Whisper Streaming](https://github.com/ufal/whisper_streaming/blob/main/whisper_online.py).
6. A complete confirmed sentence now takes precedence over an earlier hesitation,
   so a pause before a numeric qualifier does not detach that qualifier. A
   600 ms VAD pre-roll retains more of quiet phrase beginnings.
7. Actual accented YouTube speech exposed errors in the small ASR model. The
   development harness explicitly cached `Systran/faster-whisper-medium` on
   Colab, revision `08e178d48790749d25932bbc082711ddcfdfbc4f`. The consumer can
   select a cached ASR path through `LIVE_WHISPER_MODEL`; no automatic download
   or broad GPU compatibility claim is introduced.
8. Real Turkish TTS QA falsely rejected both valid "İlginç." candidates because
   Unicode casefold produces an i plus combining dot, which punctuation removal
   split into two tokens. Only that combining sequence is canonicalized;
   dotless ı and other accents stay distinct. Reassessing the same saved real
   WAVs gives WER 0, without regenerating audio or changing the original logs.
9. Session `1023708ecf7040b2983efc84cd1924db` produced five real Turkish phrases
   and completed playback of three. Actual end offsets were -0.260, -0.084 and
   -0.077 seconds. Pause/resume events were recorded, including intentional user
   hold time. This is limited evidence, not a long-session synchronization pass.
   MSE `remove()` then advanced the buffered range to 8.496 while video was at
   7.596, deleting needed frames before the next keyframe. The next seek timed
   out. The recorder now requests one-second keyframes, as supported by the
   [recording specification](https://www.w3.org/TR/mediastream-recording/#dom-mediarecorderoptions-videokeyframeintervalduration)
   and [Chromium implementation](https://chromium.googlesource.com/chromium/src/third_party/+/refs/heads/main/blink/renderer/modules/mediarecorder/media_recorder.cc).
   Actual browser revalidation is still required.
10. The same source exposed a short breath separating "support causes" and a
    continuous series of independent clauses. Short pauses no longer split
    already confirmed lexical continuation without punctuation. A conservative
    English comma + confirmed next subject/finite verb rule permits a complete
    independent clause to commit while speech continues; subordinate clause
    openings and ellipses remain uncommittable. "With that" is recognized as
    a completed pronoun phrase rather than an unfinished conjunction. These
    are explainable heuristics, not general grammatical correctness claims.
11. A real six-word Turkish target failed both seed-only continuation candidates
    (auto ASR recovered Armenian/German; forced Turkish diagnostics also found
    omitted/garbled words). The live-only bounded retry now keeps the same
    immutable speaker WAV and removes the source-language continuation text.
    One actual changed-strategy test recovered the exact unchanged target in
    Turkish (automatic language probability 0.9976, WER 0), 2.08 seconds of audio
    in 13 incremental chunks. Cold model load was 7.283 s; first chunk 0.974 s
    includes new reference encoding; synthesis RTF 0.806. This isolated test is
    not browser acceptance and does not establish general translation quality.
    The translation itself had a Turkish grammatical flaw, retained in evidence;
    the comparison proves the conditioning/QA improvement rather than rewriting
    the source target to make the metric pass. The offline defaults are unchanged.
12. A deployment mistake was identified: a long-running HTTP service retained
    imported endpointing code after files were uploaded. The service is now
    restarted on backend changes and reports source hashes snapshotted at import.
    `/api/status` and the session `ready` event expose the actual session/adaptive
    revisions; model results independently record their adapter revision. The
    deployment helper checks these against the intended files before capture.
    Earlier browser results must not be represented as validation of code that
    had only been uploaded, while separate fresh-process model runs remain valid.
13. In actual source `eefd66c75ee04b618b8fa32f8a946be7`, a complete clause ending
    at 7.92 seconds waited for the complex hold; new words then erased that
    endpoint. A CPU diagnosis using the saved actual ASR observations and Silero
    VAD now retains the verified pause and commits that unchanged clause at
    source observation 9.98. This is a controller regression check, not new
    model/playback acceptance. A complete later numeric qualifier still takes
    precedence over a held hesitation endpoint.
14. Code review found that closing tab-capture tracks during Finish could restore
    original-tab audio while delayed translated speech was draining. The
    extension now takes ownership of a selected-tab mute before releasing tracks
    and restores the pre-capture mute state after Stop or player closure. It
    preserves newer user mute decisions and adds no manifest permissions. See
    [Chrome tab audio behavior](https://developer.chrome.com/docs/extensions/reference/api/tabCapture#preserve-system-audio)
    and the [supported tab mute API](https://developer.chrome.com/docs/extensions/reference/api/tabs#mute-the-specified-tab).
    Actual browser drain testing is still required.

### Translation meaning is not established by the execution checks

Fresh-process captured-source regressions produced 6 Quality / 7 Balanced /
7 Low Latency phrases. Two modes were rerun only after the changed retry policy;
the earlier Quality run used no retry. All three are **execution** results, not
semantic acceptance. Inspection found grammatical errors in translations of
"let me underline this", variable translations of "causes", and a source-ASR
self-repair/polarity ambiguity. Those cases remain preserved. TTS ASR matching a
poor translation does not certify translation accuracy. General source meaning,
numbers/names/units coverage and extended conversational quality still need
stronger real evidence and correction before the feature can be accepted.

YouTube regular-video and live acceptance, actual dubbed A/V offset/drift, and
extended continuous playback remain open until successful evidence is recorded.
The fact that the source player and browser capture work is insufficient.
The current development harness can explicitly retain up to 90 seconds of
captured source PCM for diagnosis using `LIVE_CAPTURE_DIAGNOSTICS=1`; this is
disabled by default in the consumer service. It remains on the same computer.


## Current recovery checkpoint — 2026-10-25

The Colab CLI G4 session `adaptive-live-g4` is reachable through the existing
multiplexed SSH bridge. The VNC page initially displayed a frozen old frame;
explicit disconnect/connect restored actual input and fresh desktop frames.
The authorized regular source was then reopened in the Colab browser.
YouTube now shows **“Sign in to confirm you’re not a bot”** for
`AuZoDsNmG_s`. A manual user sign-in request was pending at that checkpoint; the user subsequently completed it, as recorded below. No cookie/profile
copying, account-credential handling, stream extraction or verification bypass
was attempted. That authentication blocker was subsequently resolved by the user. It is not a current blocker and must not be requested again unless actual browser evidence changes.

The corrected service was restarted and its imported implementation hashes
were verified. It is inactive; `nvidia-smi` reports no compute processes. The
Colab VM/desktop remains available for the pending manual sign-in. Final GPU
session teardown is therefore **not complete** and must be performed after the
remaining actual browser validation (or before an explicitly concluded run).

### Concrete fixes and bounded real revalidation

- Quality mode had committed the 85-percent AI-project statement without its
  terminal word `fail`. A VAD gap inside/near that word must not establish a
  boundary before it. A previously verified pause also now survives bounded
  ASR timestamp refinements while lexical revisions invalidate it.
- One focused **16.76-second real-time file-fed** regression after the fix
  produced four actual Hy-MT2/native-VoxCPM2 outputs without runtime errors.
  The first source/target now both retain McKinsey, AI, 85 percent and failure.
  All four use the same immutable speaker reference. No browser playback is
  claimed from this test. The Turkish rendering of `well-scoped` remains
  awkward/underspecified; successful execution and TTS ASR recovery do not
  establish general semantic or naturalness acceptance.
- Real SQLite distance testing exposed the last-36-row retrieval horizon and
  substring false matches (`the` inside `weather`). Live retrieval now opts
  into complete lexical matching, ignores common English function words,
  ranks relevant matches and searches persistent history before fetching a
  bounded result. Existing offline retrieval remains the default behavior.
- Four real Hy-MT2 requests used the persisted database with **80 unrelated
  rows between each request** (240 filler rows total). Relevant prior source
  context survived database reopen; Maya Chen, CUDA 12.8, 128, 48 kHz and
  20 ms were preserved, including the negative “do not change” instruction.
  Filler rows are deterministic storage-distance fixtures, not real model
  conversations or audio evidence. The first output's honorific placement
  (`Maya Chen doktoru`) is unnatural, explicitly a remaining quality issue.
  Model load was 4.114 s; request wall times were 0.722/0.461/0.372/0.393 s.

Focused Quality run: `temp/live-gate-a-quality-1792902301` on the VM.
Timing (four actual utterances; model first chunk is distinct from usable,
QA-approved browser audio):

| Metric | p50 | p95 | Maximum |
|---|---:|---:|---:|
| Translation seconds | 0.252 | 0.871 | 0.976 |
| Native TTS first-chunk seconds | 0.073 | 0.295 | 0.333 |
| Native TTS RTF | 0.387 | 0.462 | 0.468 |
| QA seconds, including speaker embedding | 0.165 | 0.386 | 0.425 |

Quality-run constructor time was 18.795 s. One short representative generated
WAV, exact JSON/JSONL traces, source hashes, previous three-mode traces and
current context/operational results are preserved in the workspace checkpoint
`temp/live-validation-20261025/live-recovery-validation-current.zip` (about
0.95 MB, 57 selected files). This is an internal recovery checkpoint, not a new
Desktop deliverable archive. It excludes models, browser profiles, credentials
and videos. Previous negative tests remain retained. Controller-only replays
are diagnostic; after their hypothetical commits diverge from the original
ASR windows, subsequent replay decisions cannot establish new inference
coverage or acceptance.

### Operational evidence, with exact limits

- 104 Python unit tests passed; all three Node regression scripts passed.
- 98 project Python files compiled; live server/model/session and main GUI
  imports succeeded. CLI and optional live-service `--help` exited zero.
- Real main-GUI `/` and `/api/status` returned HTTP 200. Live mode is present
  in the main GUI HTML. This is HTTP evidence, not rendered UI acceptance.
- Real live API returned 200 for status/player and 403 for an unpaired Origin,
  wrong Host, unauthorized pairing and unauthorized ticket creation. Player
  responses include CSP and HttpOnly/SameSite=Strict cookies; credential
  values were not written to the reports.
- Python 3.13 removed `cgi`; the Colab-only GUI probe used the 20 KB
  `legacy-cgi==2.6.4` compatibility package, as identified by the
  [Python documentation](https://docs.python.org/3.13/library/cgi.html).
  Consumer dependency defaults were not changed for this development image.
- **Base offline setup check exited 1**, with six required failures: Python
  3.13 instead of the pinned 3.10 stack; missing `whisper`, `whisperx` and
  `resampy`; missing vocal-remover weights; and VoxCPM2 absent from the
  offline checker's default model path (the real live tests explicitly use
  `/content/voxcpm2`). These are limitations of the minimal live Colab image.
  No offline weights were downloaded merely to satisfy this check.
- Source-only scan: 140 source/doc/config files, no matching credential
  literals and no unexpected source files over 25 MB. Known generated/model
  directories were excluded; this is not a claim to scan every historical
  private diagnostic or third-party cache.
- Approved repaired-video SHA-256 still matches
  `9b296ece3680d2739ee0616710f303ed7040c2ffff341c3ab4ed0307fe2053e7`.
  The workspace has no `.git`; a Git diff/stat is unavailable. No commit,
  push, PR, remote branch or publication operation was performed.

Remaining: actual VOD/live browser acceptance, real Finish/drain/audio-mode
restoration checks, sustained multi-speaker playback and queue/drift stability,
remaining semantic/naturalness weaknesses, and final teardown. The original
live-dubbing goal is **not complete**.


## Authenticated YouTube playback and source-boundary investigation

The user completed Colab-browser sign-in. The authorized VOD opened and played;
YouTube's settings visibly showed **Normal** source speed. No account cookies,
profile data or credentials were copied. All capture and inference remained on
that same Colab computer, through its loopback service.

Actual browser runs retained on the VM and in small workspace checkpoints:

| Session | Actual evidence | Failure / acceptance status |
|---|---|---|
| `0b87871cfdb14365af9d8a5588f70ad8` | Real video/PCM capture and medium-ASR windows | Could not split a coordinated clause starting “and in previous years”; zero generated phrases. Added bounded preposed-modifier/free-relative handling, verified against the same ASR observations and then retried |
| `e55ac5c28b4145a7b8426183b41fe601` | Real capture started while source was paused | Delayed player stayed at time zero through an initial silent interval and reached its 20-second limit; unresolved media-startup failure, not model-throughput proof |
| `504478dd94574961a81c95f8c8973bd4` | Source already playing; four native outputs, three actual playback ACKs | Later source uncertainty deadline; no full acceptance |
| `7a62fac16c3f40ecacbd8352a8cbb7c7` | Seven native outputs, six actual playback ACKs; frame callbacks and AudioContext clocks | Later source uncertainty deadline; a pause event arrived, but no resume before the error. Finish/drain and audio-mode switching remain unverified |

For the last run, absolute end-of-phrase A/V offsets over six actual playback
ACKs were p50 **87.5 ms**, p95 **108.25 ms**, maximum **110 ms**. These compare
real video-frame callback times with the generated-audio source endpoint map;
they do not certify physical output latency or phoneme-level lip sync.
Effective buffering was substantially above the engineering 1–3 second targets.
This is **partial/failed** acceptance evidence, not a production-readiness claim.

A 12-second excerpt of the actual screen + PulseAudio-monitor recording is
preserved at `temp/live-validation-20261025/partial-youtube-dub-12s.mp4`.
It contains real browser playback audio; a generated WAV was not substituted.
Automatic medium-ASR identified Turkish with probability 0.9990 and recovered
“... AI alanında kariyer tavsiyeleri hakkında konuşmak istiyorum ... önceki
yıllarda bu dersin çoğunu tek başıma yapıyordum.” RMS was 0.06135, peak 0.72360.
This confirms audible Turkish playback in that excerpt, not full semantic
correctness or the absence of every source-language word in the entire run.
The exact original recovery text/metrics remain in
`temp/vod-actual-playback-case-current/actual-playback-audio-check.json` on Colab.

### Current source fixes and experiments — do not conflate their status

1. The live ASR buffer previously trimmed exactly to an estimated word onset
   after a commit. It now retains 600 ms of preceding acoustic context and
   records the actual ASR window start/duration. A paired real-audio probe
   recovered “He lives in Seattle” where the exact-boundary window produced
   “this list is Seattle to”. Other windows remained imperfect; this is not a
   blanket accuracy claim. The new overlap uses existing timestamp/suffix
   deduplication and does not alter TTS reference boundaries.
2. Confirmed sentence/clause endpoints now survive bounded alignment and
   punctuation revision during extra holding time. Further actual testing
   showed that this can also preserve an incorrect early ASR period. Plain
   punctuation remains insufficient evidence of complete meaning; early
   adjective/recipient/disfluency cuts are still under investigation.
3. An optional CPU-only `EnglishSyntaxGuard` exists behind
   `LIVE_SYNTAX_MODEL=en_core_web_sm`, with a bounded parse cache and explicit
   cleanup. It loads only an already installed pipeline, never downloads one,
   and is **off by default**. Colab already had spaCy 3.8.16 / en_core_web_sm
   3.8.0 installed. The [spaCy dependency/POS documentation](https://spacy.io/usage/linguistic-features#dependency-parse)
   informed this experiment; statistical syntax is not semantic certainty.
   Fourteen initial contrast cases exposed one false rejection, corrected by
   handling the subject attached through a finite auxiliary. Real ASR-only
   testing still exposed a disfluent parenthetical and a missing recipient
   preposition. The option has **not** passed full real browser/translation
   acceptance and has not been made the default.
4. The opt-in path gives a long named dependency at most two additional seconds
   of confirmation (absolute source-phrase budget at most 16 s); no deadline
   forces an unsafe commit. This addressed the observed 12-second cutoff while
   the final place name was still provisional. It is not a global buffer-cap
   increase, and browser buffer limits remain unchanged.
5. Two bounded wall-clock ASR-only diagnostics used the same recorded source,
   persistent real medium-ASR and real Silero. They intentionally omitted
   translation, TTS, speaker matching and playback; their synthesis queues were
   only observed/drained. Both stopped on unresolved source uncertainty. They
   are **negative component evidence**, never full-pipeline passes. Unique
   directories are recorded by `temp/asr-endpoint-component-latest.txt` on
   Colab; previous result directories and logs remain preserved.

Repeated medium-ASR failures justify one stronger-ASR accuracy comparison.
The official [Systran CTranslate2 large-v3 conversion](https://huggingface.co/Systran/faster-whisper-large-v3)
was fetched only to `/content/faster-whisper-large-v3`, pinned revision
`edaa852ec7e145841d8ffdb056a99866b5f0a478`; its model.bin is 3,087,284,237 bytes.
No ASR weights were downloaded to the Mac. This is a development comparison,
not a consumer-default change or a broad hardware certification. No Hy-MT2
7B/30B weights or Modal services were used. Short matched-window results must
be inspected before any production-model selection or another full browser
retry.

Latest unit run: **113 tests passed** on Colab. Unit/grammar tests do not close
any real inference, semantic, YouTube-live or sustained-playback gate. The
original goal remains active and incomplete.


### Stronger-ASR comparison and infinitive boundary repair

Matched real Large-v3 windows preserved the handover recipient more reliably
than the earlier medium stream, but a 15-second acoustic window introduced
“hand it over. It is my friend” in four early windows. No long-audio-window
change was adopted. Raw probe: `temp/large-asr-long-audio-context-comparison.json`.

Large-v3 alone completed the 37.54-second wall-clock ASR/VAD component test,
but cut “who I invited / to speak here”; the initial optional syntax test also
missed this. The guard followed only the immediate head of “to”, missing its
infinitive verb's attachment to “invited”. It also mistook a root infinitive
for an imperative. The guard now checks the attachment ancestry and rejects
an infinitive introduced by “to” as a standalone imperative. A contrast test
retains the valid imperative “Speak here.”.

The corrected, opt-in Large-v3 + syntax component run completed in 38.694 s
with a 1.127 s ASR model load and four commits. It retained the disfluent
lecture sentence and the complete handover through San Francisco, without
those previously observed cuts. The final “he lives in Seattle, to share...”
still requires contextual translation review: parsing is not semantic proof.
This harness performed NO translation, TTS, speaker processing or playback.
It is component evidence only. No consumer default was changed; a real browser
retry is required before accepting this development configuration.

Latest unit suite: 114 tests passed on Colab. Added real-player telemetry
exposes original-audio selection, preview mute and translated gain so the
browser audio-mode control can be assessed from actual session observations.


The first integrated Large-v3/guard browser retry (`2c72a00c...`) stopped at
source 25.84 s. Real hypotheses show proper-name capitalization disappearing
at 23.4 s, shrinking its named-dependency budget back to 12 seconds. The guard
now retains previously observed named-dependency complexity until commitment;
the opt-in bounded confirmation budget is at most 16 seconds (up to four extra
seconds, superseding the earlier two-second experiment). This never forces an
uncertain commit. The same fixed 20-second video cap was inconsistent with a
16-second allowed source phrase plus the existing 12-second processing deadline.
The ready handshake now supplies a bounded video allowance derived from those
two budgets plus the mode's initial delay, hard-capped at 40 seconds: 31 seconds
for this development Balanced/guard configuration. This is capacity admission,
not an inference speedup or a latency-target pass. The 32 MB encoded limit,
2-second ingest queue, 3-phrase synthesis queue and 12-second processing-age
fallback remain bounded. This retry generated two phrases and actually played
one; it did not validate resume, audio switching, finish or sustained playback.


### Actual VOD controls, five played phrases and terminal media repair

Authenticated session `1e38337e5f56491fbcc6426593667f67` captured 47.168 seconds
of real arriving YouTube media. Five native phrases were generated and all
five were actually played, with zero duplicate acknowledgements and no unplayed
phrases. One real bounded QA retry was used. A paired presentation pause lasted
6.387 s. Six actual telemetry samples in Original mode had preview unmuted and
translated gain 0; 62 in Translated mode had preview muted and gain 1. Finish
transferred capture mute to the extension before track release; five remaining
outputs drained. This is actual UI/model/playback evidence, not mocked controls.

Absolute endpoint A/V offset: p50 127 ms, p95 139 ms, max 141 ms (five ACKs).
Speech-end-to-playback p50 14.082 s, p95 17.235 s, max 17.963 s; TTS TTFA p50
118 ms / p95 300 ms, RTF p50 .614 / p95 .707. Queue depth max 2, encoded bytes
max 6,748,954, observed buffer max 24.838 s. These exceed latency targets and
are not extended stability or physical/phonemic lipsync certification.

Semantic review remains limited: the second target omits the explicit lecture
noun; the deliberately cut final source preserves an unfinished “it really -”
and its Turkish fragment is awkward, including erroneous “devredemeden” for
“before I hand it over”. The four earlier statements retain their main meaning;
this does not establish general semantic/naturalness acceptance.

After all five ACKs, calling native video.play() near MSE EOF rewound the
preview to zero, causing a false 31-second backlog error. The terminal check
now waits for final recorder append / MSE ended, marks the player drained
before scheduling play, clamps the available frontier to actual buffered end,
and prevents pending play promises from restarting ended/drained media.
A separate actual Chrome 4.5-second existing-file capture → MediaRecorder →
MSE regression reached drained at video 4.460166 s with 130 presented frames,
MSE ended and no rewind. It used no inference and closes only the media-control
regression; the full native browser path with this terminal fix still needs
validation. JSON was saved by the actual browser on Colab.

Small checkpoints: `temp/live-validation-20261025/live-vod-controls-evidence.zip`
(1,291,743 bytes) and a 12-second excerpt of actual screen/PulseAudio output,
`actual-youtube-controls-partial-12s.mp4` (186,738 bytes). No generated WAV was
substituted into that recording. The original goal remains incomplete.


### First actual live-stream inference and continuous recording clock

The supplied `DFYaNjzI1aI` Talk24/7 page visibly showed a real live stream and
was playing after its normal ads completed. Session `3f06f037...` captured
actual arriving speech on that Colab computer and generated two real Turkish
native outputs. It stopped at source 19.66 s on an unconfirmed ending; zero
complete playback ACKs arrived. Initial video frame progression did not match
audio (last observed mapped A/V offset about -2.77 s). This is failed actual
live acceptance, not a substitute file benchmark or a passed P0 gate.

One source boundary was the complete assertion “the real life effects they're
not funny / but when you ...”. The optional syntax path now permits a complete
assertion before an overt contrasting conditional; it still rejects a bare
following “if” which could qualify that assertion. Complete clauses with an
initial conditional plus a real main predicate are no longer rejected merely
for beginning with “if”. The real input, failure and machine metrics are
preserved in `live-first-and-media-fixes.zip` (552,381 bytes).

A paired actual Chrome experiment used the same permitted file, held paused
for two seconds then played for 4.5 seconds, recording raw and continuous-audio
variants simultaneously. Raw MSE ended at 5.432066 s / 164 frames; the continuous
clock path ended at 6.486315 s / 194 frames. Both reached drain, but raw lost
about one second of the captured wall-clock interval. This supports the
startup timing defect, rather than treating a GPU allocation or larger buffer
as a solution.

The consumer MediaRecorder input now uses original source audio at unity gain
plus digital zero from a 48 kHz browser AudioContext. It retains time through
initial silence, stays entirely in the same browser, and changes neither the
original ASR Worklet PCM nor the target speech. The recording clock and its
owned audio track are closed when capture ends or Stop occurs. A separate real
consumer-path regression, two seconds paused plus 4.511537 s playing, drained
at video 6.520567 s with 196 presented frames and MSE ended, without rewind.
These media tests involve NO inference and close only this component defect;
the revised native YouTube path still requires an integrated browser retry.

Latest unit result: 115 passed; all three JavaScript regression scripts passed.
No consumer ASR/syntax default was changed. No goal-completion claim is made.


### Static-frame cadence, first-frame readiness and ordinary complex clauses

A later actual live run (`0d414f32...`) covered 57.68 s during a music/bumper
transition followed by speech. Captured audio was nonzero (RMS .04565, peak
.17667); source speech was eventually detected and one phrase committed, but
manual Stop preceded any completed TTS output. During a static logo the video
frame time stopped while PCM and encoded buffer time advanced. Sparse video
cadence and random-access pruning remain possible contributors; no raw encoder
trace established a driver-level cause. It is not a successful dubbing gate.

The recording path now draws the latest authorized video frame to an in-browser
canvas at 24 fps, alongside the continuous 48 kHz source-audio clock. Only the
recorder input changes; no video goes to inference and no source speech is
fabricated. Its owned canvas track, video element, timer, zero source and audio
clock are released at capture end/Stop. A real six-second pause + 4.51994-second
file playback test drained at video 10.487213 s, with 256 presented frames.
This tests the media mechanism, not the live model or semantic gates.

Actual native session `a1e934f7...` then exposed a separate leading-frame gap:
its first frame was at .624 s, but a phrase began near zero. Seeking the preview
back into that missing range left readyState 1 while target audio could start.
The canvas now paints a zero frame while awaiting source frames, including an
explicit paint after recorder.start. Phrase start waits for real video data
(readyState >= 2) within a three-second bound before creating the audio source;
a failed wait is explicit and plays no translated audio. A regression verifies
that no audio source is created until the actual readiness event.

That native run also timed out a normal 12-second complex question, even though
its final noun was confirmed and the next contrasting clause was arriving.
The optional syntax path's existing at-most-16-second confirmation allowance
now applies to observed long/dependent clauses regardless of proper-name case.
The observed complexity is retained until commitment. This supersedes the
name-only allowance; it does not force a prefix on timeout, change offline
settings or increase the 18-second uncommitted PCM / 31-second development
video-buffer limits. Real integrated validation is still required.

### Actual tab clock root cause and ten-phrase live session (2026-10-25)

The recorder timing defect was isolated on the **selected, arriving YouTube live
source**, without inference. The timer-driven zero-rate canvas encoded only 164
video packets in 12 seconds, with a 1-second WebM DefaultDuration and gaps up to
1 second. Its MSE seek to .04 seconds never completed. The current recorder uses
`captureStream(25)` and paints every second actual 20-ms input Worklet packet.
The matching actual-tab probe produced 301 packets, .040-second DefaultDuration,
maximum .049-second gap, and completed the same seek in .0081 seconds. It drained
normally with 299 presented frames. These are real media component results, not
an inference or YouTube dubbing acceptance certificate.

Actual native live session `6da0b06f9c264cdab8a1bb863dc5ce3a` then produced and
**actually played ten phrases**, with no duplicate acknowledgements. Three
requests used their single permitted QA regeneration. Absolute end-of-phrase
A/V offset was p50 .0805 s, p95 .4536 s, max .711 s; these compare frame callbacks
with generated-audio endpoint mapping, not physical device latency or phonemes.
The session eventually stopped explicitly on uncertain/incomplete syntax, so it
does **not** pass extended stability. Its source covered 47.5 seconds.

Two meaningful source defects were investigated with actual captured audio:
`£7.4` had committed without the following `billion`, and a cropped previous word
(`think tank`) became a false new `Bank`. The quantity guard now checks both the
confirmed prefix and provisional continuation before commitment. ASR windows
retain the whole previous committed word, bounded to 1.5 seconds of acoustic
context, instead of always cropping at a fixed .6 seconds. Paired real Large-v3
decodes on the same source inserted Bank in 4/6 old-window runs and 0/6 corrected
runs. No expected text prompt or manual deletion was used.

A real 32.2-second wall-clock 20-ms ASR/Silero/controller regression, with .8 s
known end silence, completed in 33.305 s without failure. It committed the full
`£7.4 billion` sentence and the full `782 million ... cost of living crisis`
clause, without Bank insertion. This harness explicitly does not synthesize,
assign speakers, or fabricate playback acknowledgements.

The two corrected clauses were then rendered by one warm native GPU model set:
load 19.111 s, total 28.367 s, TTS RTF .376/.347, TTFA .197/.056 s, no QA retries,
one immutable conditioning-cache miss followed by one hit. Number magnitudes
were preserved and both generated utterances passed signal/ASR recoverability
checks. **Semantic failure remains:** Hy-MT2 translated `household council tax`
as `ev holding vergisi`. Duration, no-context, and official-simple prompts all
mistranslated it in six bounded real comparison generations. Prompt changes were
therefore not adopted. Passing target-speech ASR does not validate translation
meaning. The arrears/cost-of-living clause retained its meaning, with the less
specific Turkish `borçlar` for arrears.

A separate actual playback outlier showed native target speech ending before
its corresponding video could finish even at the allowed 1.5x video rate.
Presentation buffers now retain all original generated PCM and, only when
needed, append silence to allow the source video interval to finish at that
rate. Generated duration/RTF/QA remain based on the native waveform; played
acknowledgements separately record generated, presentation and padded seconds.
This mechanism has a JavaScript regression; integrated real browser validation
is still pending at this checkpoint.

Current full Python suite: **118 passed**. All three JavaScript regression
scripts passed. Consumer ASR and syntax defaults remain unchanged; Large-v3 and
the CPU syntax guard are explicit development overrides. Small local evidence:
`temp/live-validation-20261025/corrected-live-components.zip`,
`real-youtube-live-ten-played-evidence.zip`, `live-tab-clock-root-cause.zip`.
No goal completion or production-readiness claim is made.


### Restored G4/VNC and real EOS/PCM boundary corrections (2026-10-26)

The expired VM was replaced through Colab CLI G4; sandboxed Chrome/noVNC and
localhost-only services were restored. No previous offline E2E or language
benchmark was rerun. No large model file was downloaded to the Mac.

Actual main-GUI navigation exposed an existing-service launch collision.
`launch_live_service` now probes the owned app identity and reuses a healthy
localhost service without taking process ownership. Real GUI navigation and
service reuse passed with no new duplicate-bind errors and no model/capture
autostart. A foreign/unresponsive listener still produces an explicit error.

The real retained live capture exposed EOS approval of an incomplete final
clause when a fresh ASR decode replaced its ellipsis with a period. EOS now uses
normal endpoint selection, drains complete confirmed endpoints with monotonic
source-clock progress, and reports the remaining tail after two non-progressing
decodes. Stop alone cannot confirm an unpunctuated active-speech cutoff. The
optional English guard detects the observed missing complement after `make`,
while retaining valid object-relative and object-predicate constructions. This
is an explainable limited heuristic, not general semantic certification.

One warm real Large-v3 comparison on captured source 5.68–16.328 s approved the
whole cutoff before the fix; after it, only the complete hope/pray/healthy
clause committed and source 8.44–16.328 s remained explicitly untranslated.
A separate real-time 20-ms feed of the same 10.648-s crop completed in 11.347 s
with the same safe result. This component seeded only the previously committed
source boundary, supplied no expected ASR words, and did not synthesize or
fabricate playback. Earlier invalid/negative diagnostic attempts are retained.
The earlier Catherine participial-fragment rejection remains outside this fix.

Actual VOD browser session `c5c5e35182504724b8ad470d5648eb30` then generated and
played one native Turkish target (4.16 s; TTFA .449 s, RTF .759; frame/audio
endpoint offset -.151 s; speech-end-to-playback 12.129 s). It began mid-source
utterance and misrecognized “tips for career” as “It's for career”, so it is
not a semantic pass. A second long clause became fully confirmed at source
21.34 s but hit the physical 18-s PCM cap during its .85-s confirmation hold.
Capture stopped explicitly and GPU workers released their memory. This run
does not validate the corrected browser EOS path or extended stability.

Physical PCM capacity now derives from the maximum permitted source interval,
1.5 s of retained acoustic context, the unchanged semantic hold, two ASR
cadences and a bounded packet allowance (19.59 s in Balanced with the explicit
syntax override). It remains bounded; semantic uncertainty time is unchanged.
The full failed 21.86-s captured WAV plus **1.2 s of declared known silence**
was then re-fed in real-time 20-ms chunks through actual Large-v3/Silero and the
controller. Both clauses committed without error or untranslated tail; the
second used `held_confirmed_sentence`, not forced EOS. Run time was 23.111 s,
model load 1.054 s. This is ASR/buffer component validation only. Corrected
integrated browser playback and extended quality acceptance remain open.

Current full Python suite: **128 passed** (4.228 s on restored Colab). Earlier
compile/import/CLI help and three Node regressions passed. The base offline
setup checker still has seven environment/default-cache failures in this
development-only Python 3.13 VM; Live's explicitly installed model paths are
ready. Do not report fresh offline installation certification from this VM.
Approved offline MP4 remains 3,915,948 bytes with SHA256
`9b296ece3680d2739ee0616710f303ed7040c2ffff341c3ab4ed0307fe2053e7`.

Current detailed status, all twenty deliverables and machine-readable evidence:
`temp/live-validation-20261026/STATUS_REPORT.md`, `recovery-evidence.zip`,
`native-vod-negative.zip`, `pcm-headroom-evidence.zip`. The prior thirteen-played
real live session, actual EOS partial-tail drain and all prior negative results
remain preserved in `temp/live-validation-20261025/`. Meaning quality, name
accuracy, long-clause coverage and sustained multi-speaker YouTube acceptance
remain substantial open engineering gaps. Goal remains active; no publication
or production-readiness claim is made.


### 26 October: targeted semantic comparison and real endpoint failures

The subsequent checkpoint is recorded in `temp/live-validation-20261026/STATUS_REPORT.md`. The actual VOD reached 113.64 seconds with ten real played acknowledgements before explicit processing backpressure; semantic quality and sustained coverage failed. Two real-time native replays exposed first a stranded predicate, then a repeated topic/list boundary. Both negatives were preserved before narrow dependency/topic fixes; 132 unit tests passed after the topic fix. These are engineering diagnostics, not completed YouTube acceptance.

After explicit human authorization, Hy-MT2 7B was downloaded and run only on Colab G4 (official revision `9b0eb4e8f001def3e5ff6469a0ac96fdb39ec223`). Four short unassisted/actual-source-context outputs improved council tax and idiomatic phrasing, but the doubling-time technical term remained defective. Generation .412–1.233 s, all EOS, 25–57 tokens; no default model or decoding change. The initial diagnostic download omitted `chat_template.jinja`; its traceback and corrected retry are retained. This was a harness asset-filter error, not proof that the official model lacks a template. No 30B download, no model on the Mac, no full offline rerun.

Live optional terminology uses an explicit JSON mapping, at most 50 single-line string pairs of 160 characters each. Only matching current-source entries reach translation. It does not bias the independent TTS QA recognizer. Assisted terminology results must be labeled separately from unassisted translation quality. No terms are fabricated from expected test transcripts.
