# Security policy

## Reporting a vulnerability

Please do not publish exploitable details in a public issue. Use GitHub's private vulnerability reporting for this repository when available. If that option is not visible, open a minimal issue asking the maintainer for a private contact channel without including the vulnerability details.

Include the affected file/version, reproduction conditions, realistic impact, and any suggested mitigation. Do not include private media, transcripts, credentials, or model files.

## Scope and deployment warning

The browser GUI is a single-user local tool. It binds to `127.0.0.1` and has no authentication, authorization, upload quota, tenant isolation, or automatic retention policy. Do not expose it to a network or the public internet without adding those controls and completing a dedicated security review.

Model checkpoints are deserialized by third-party ML libraries. Use the pinned download sources, keep checksum verification enabled where provided, and do not substitute untrusted checkpoint files.
