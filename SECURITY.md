# Security policy

VeriScrub processes untrusted files, so parser and subprocess bugs matter. Please report them privately.

## Reporting a vulnerability

Use **GitHub's private vulnerability reporting**: go to the
[Security tab](https://github.com/CodeWithLakxsh/veriscrub/security), then **Report a vulnerability**.
Please do not open a public issue for security problems.

Include the VeriScrub version, the output of `veriscrub doctor`, and a minimal reproducer. Make it a synthetic file if you can, and never a real personal one.

## In scope

- Private metadata reported as removed while it is still present in the output (a false `clean`).
- An original file being modified, or an existing file being overwritten.
- A crafted file causing command execution, path traversal, symlink following, or excessive resource use.
- Metadata values leaking into reports without `--show-values`.

## Out of scope

- Watermarks, steganography, AI-detector results, or signals inside pixels or audio samples. VeriScrub does not claim to handle these. See `skills/veriscrub/references/provenance.md`.
- Vulnerabilities in ExifTool, FFmpeg or qpdf themselves. Please report those upstream.

## Supported versions

Only the latest release receives fixes.
