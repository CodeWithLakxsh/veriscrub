# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Unreadable files (permission denied, or the macOS Photos library) are now reported as `failed` with a clear hint. Previously they appeared as `inspected` with no fields.

## [0.1.0] - 2026-11-06

First public release.

### Added

- `veriscrub` CLI with `doctor`, `inspect`, `clean` and `verify`, human-readable and `--json` output, dry run, batch processing into an output directory, and collision-safe publishing.
- Agent Skill at `skills/veriscrub/SKILL.md` for Claude Code, GitHub Copilot, OpenAI Codex and other Agent Skills clients.
- Handlers:
  - **JPEG, PNG and static WebP** through ExifTool, plus a built-in PNG text-chunk filter for AI generation fields.
  - **MP4, MOV, MKV, MP3, M4A and FLAC** through FFmpeg stream copy.
  - **PDF** through a pikepdf full rewrite.
  - **DOCX properties** and **Markdown front matter**, both built in.
- Verify-before-publish. Each output is re-opened before it is published. The checks cover removed fields, image data and pixel hashes, packet hashes, duration and rotation, page content, DOCX parts, the Markdown body, removed-value residue, and the hash of the original.
- Content Credentials (C2PA) detection. Signed and encrypted documents are detected and skipped. Videos with streams that cannot be preserved are explained and skipped.
- Synthetic test fixtures, plus CI on Linux, macOS and Windows.

[0.1.0]: https://github.com/CodeWithLakxsh/veriscrub/releases/tag/v0.1.0
