# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `clean --report` writes `<name>.removed-metadata.txt` next to the cleaned file. It lists every removed field with its value, the GPS position in decimal degrees with a map link, the kept fields and all verification checks, under a "private, do not share" warning.
- `clean --zip` publishes one `<name>.zip` containing the cleaned file and that report. The zip is re-opened and its cleaned file is hash-checked before it is saved.

- `people` category, removed by default. It covers face regions and face IDs (Apple Photos and MWG/Microsoft region tags) and names of people in the photo.
- JPEG MPF secondary images, such as the iPhone HDR gain map, are extracted and inspected instead of being reported as uninspected data. A new `mpf_images_preserved` check confirms they are still reachable after cleaning.

### Fixed

- MPF offsets are recomputed after cleaning, because ExifTool 13.x leaves them stale when it deletes a segment such as `COM`. Previously the secondary images could become unreachable.
- A removed value that legitimately remains inside a retained field (for example the GPS date inside `DateTimeOriginal`) no longer counts as residue.
- `XMP-photoshop:DateCreated` is now classified as `timestamp`, and Apple HDR (APP10) and gain-map URN (APP2) data as `technical`.
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
