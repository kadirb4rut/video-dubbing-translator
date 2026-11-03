# Demo media status

The repository contains a real two-speaker before/after pair from the existing offline workflow. The original and dubbed MP4s are retained in local development/test locations, along with speaker references, audio, transcripts, and reports. They are not copied into the release assets directory.

The source file includes a creator/title attribution in its media metadata, but the repository does not contain a license record or written redistribution permission for that recording. A translation/dub is still a derivative media artifact. Until permission is recorded for the source picture, dialogue, and any background music, keep both videos and their source audio out of the public repository, GitHub Release, and VoxCPM Discussion.

For YouTube-origin material, the platform lists the Standard YouTube license as the default; reuse requires an applicable service permission, rights-holder permission, a valid Creative Commons grant, or another applicable legal basis. Attribution by itself does not grant reuse rights. Confirm the exact license and attribution for the particular source before distribution: [YouTube license types](https://support.google.com/youtube/answer/2797468?hl=en), [YouTube Terms](https://www.youtube.com/t/terms), and [YouTube copyright help](https://support.google.com/youtube/answer/2797466?hl=en).

The older 15-second asset at `docs/assets/demo-comparison-15s.mp4` predates Hy-MT2 and multi-speaker support and is not evidence for the new core workflow. Its public redistribution clearance is also not established in this repository; it is not selected as the new release demo.

Use a cleared two-speaker source or obtain written permission before adding these items to a public release:

1. Original/source video.
2. Turkish dubbed output video.
3. A duration-refinement example with the matching source/output audio and the measured target/initial/refined durations.
4. Attribution and license terms for every source element, including music, image, and voice.

Current local before/after files and the private report index are identified in the maintainer's local completion report. The shareable technical report contains aggregate measurements only and omits transcript text and media.


November 1 update: the full NASA/Colab output and short synthetic Modal test video
are additional local verification artifacts, described in
[GUI compute E2E](GUI_COMPUTE_E2E_20261101.md). They were not selected or attached
as public release assets by the UI/report work. A new test result does not by
itself close the source-specific redistribution/attribution record above.

## Historical November 3 proposed release media

This pre-upload status was superseded by the public media approval below.

Four NASA JWST files and a YouTube Live screen recording are staged outside the repository. The NASA interview source is identified below; the proposed synthetic-voice use and the Live recording’s third-party broadcast rights still require review. Do not infer permission from capture or attribution. Public upload requires rights review and explicit publication approval. No GitHub asset URLs or playback verification exist for these files yet. The original files remain untouched. See `MEDIA_VERIFICATION.md` for technical results.

## NASA interview provenance — November 3

The [NASA Scientific Visualization Studio item 14349](https://svs.gsfc.nasa.gov/14349/) lists a 3:49 pre-recorded interview with Dr. Amber Straughn, includes the question shown in the local demo, and asks for credit to NASA's Goddard Space Flight Center. The official WebM's encoded audio packets have the same SHA-256 as the local English MP4's encoded audio packets (`7fd2851e9af4bf45fb277e349f704b7ea3064a42be7d6309479769467bb801fe`); sampled frames at 0, 20, 30 and 49 seconds also visually match within encoding/frame-selection differences. This supports the identified footage provenance; it is not an independent rights clearance for every element or derivative.

[NASA's media guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/) distinguish factual informational use from promotional and commercial use, request source acknowledgement and prohibit implying endorsement; they also warn about third-party material and identifiable people. Review the intended repository/demo presentation, synthetic voice and any applicable permissions before distribution. The unrelated Live recording contains third-party broadcast imagery and remains subject to its own rights review. User authorization for public uploads is still required.

## Historical initial authorization and Live safety check

The initial hold below was superseded by the maintainer's explicit approval recorded in the following section.

The maintainer confirmed rights for all five videos and explicitly authorized public GitHub media upload and playback testing, while withholding authorization for a `main` push, tag or Release publication. A pre-upload review of the full Live recording found an on-screen one-use pairing code around 00:56–00:59, a local machine identifier, and a cloud-compute balance. The user instructed that any recording containing exposed credentials or private identifiers must not be uploaded, so the Live upload is withheld. The credential itself is intentionally not recorded here. Do not treat expiration as permission to publish it. The four Offline clips are separate.

## Public media approval — November 3

The maintainer explicitly confirmed rights and approved publishing the complete unredacted Live video, including the expired one-use pairing code and acknowledged UI information, and one public media-only issue. The five approved assets are in [issue #4](https://github.com/kadirb4rut/video-dubbing-translator/issues/4). The 67.5 MB Live upload failed; the complete 9.46 MB version was used after checking text readability. The maintainer did not authorize a `main` push, tag or Release publication.
