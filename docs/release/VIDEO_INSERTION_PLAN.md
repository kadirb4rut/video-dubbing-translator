# README video integration status

The maintainer authorized one public media-only issue and the complete, unredacted Live recording. The five GitHub attachments are hosted in [issue #4](https://github.com/kadirb4rut/voxadub/issues/4). The staged README uses a standalone Live attachment and the same 2×2 table markup exercised in that public issue. The four Offline clips use the common 00:20–00:50 source interval.

The preferred 67.5 MB Live file was rejected by the attachment interface; the 9,459,417-byte full-duration fallback was used after comparing UI readability. It retains all 7,770 decoded video frames and copies the original AAC stream; 28 source non-monotonic frame timestamps were corrected, by up to 49.667 ms. Details and checks are in [media verification](MEDIA_VERIFICATION.md).

The public issue rendered all five native players and its Offline table in the requested order. The release-candidate README renders all five players and the same 2×2 layout on GitHub; playback, pause, seek, resume and end states were exercised. On narrow screens the table scrolls horizontally. Independent listening and final publication remain open.

This issue does not authorize a `main` push, a version tag, a GitHub Release, or unrelated publication. The historical `demo-videos` release is unchanged.
