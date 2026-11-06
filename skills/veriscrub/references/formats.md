# Format coverage and limitations

Every `clean` follows the same pipeline:

1. Detect the real type from the file's bytes. The extension is only checked for mismatches.
2. Inspect the file and decide whether to skip it (signed provenance, digital signature, encryption, or data that cannot be preserved).
3. Write the cleaned copy into a private temporary directory next to the destination.
4. Re-open the output with the same inspector, check integrity, and scan the bytes for removed values.
5. Publish the output with an atomic, no-overwrite link or rename. A failed check means nothing is published.
6. Re-hash the original to prove it was not changed.

`partial` means the output was published but something privacy-relevant remains. This can be a field the backend could not delete, data outside the handler's scope, or a removed value still present in the bytes. `--strict` turns `partial` into `failed` and publishes nothing.

## Matrix

| Format | Backend | Inspect | Clean | Verified after clean |
|---|---|---|---|---|
| JPEG | ExifTool + built-in parser | EXIF, IPTC, XMP, COM, MakerNotes, face regions, MPF secondary images, JUMBF | yes | targeted tags absent (ExifTool re-read), compressed image data + ICC + MPF images identical, orientation identical |
| PNG | ExifTool + built-in chunk filter | eXIf/EXIF, XMP, tEXt/zTXt/iTXt by exact keyword, tIME | yes (static) | as JPEG, plus PLTE/tRNS/gAMA/cHRM/sRGB/iCCP chunks identical |
| WebP | ExifTool + built-in parser | EXIF, XMP chunks | yes (static) | as JPEG, plus alpha flag, canvas size and ICCP identical |
| MP4, MOV | ffprobe + FFmpeg stream copy | container and stream tags, Apple `mdta` keys, chapters, stream types | yes (audio/video streams only) | stream layout, per-stream packet SHA-256, duration (±0.1 s or 1 %), rotation, 2 s decode |
| MKV | ffprobe + FFmpeg stream copy | segment and track tags | yes (audio/video streams only) | same as MP4 |
| MP3 | ffprobe + FFmpeg stream copy | ID3v2/ID3v1 tags, APIC artwork | yes | same as MP4; artwork kept unless `--remove-artwork` |
| M4A | ffprobe + FFmpeg stream copy | iTunes-style tags, `covr` artwork | yes | same as MP4 |
| FLAC | ffprobe + FFmpeg stream copy | Vorbis comments, PICTURE blocks | yes | same as MP4 |
| PDF | pikepdf (qpdf) | Info dictionary, document XMP, signatures, encryption, annotations, attachments, object XMP, PieceInfo | yes | Info/XMP keys absent, page count + decoded page content identical, byte scan for removed values, single revision |
| DOCX | built-in (zipfile + XML) | core, app and custom properties; counts comments, tracked changes, people, embeddings, media, thumbnail, rsids | yes (properties only) | every part outside `docProps/` byte-identical, same member list and order |
| Markdown | built-in | YAML front matter keys | only `--front-matter-key` keys | body byte-identical, other front matter lines identical |
| TXT | built-in | reports that plain text has no metadata fields | n/a (`skipped`) | n/a |
| Other (GIF, TIFF, HEIC, ...) | ExifTool, if installed | read-only | `unsupported` | n/a |

## Images (JPEG, PNG, WebP)

- Deletion is per tag (`-GROUP:Tag=`). GPS, IFD1 thumbnails and MakerNotes are removed as whole groups. ExifTool's own `XMPToolkit` stamp is suppressed when XMP is rewritten.
- ExifTool 13.55 cannot delete PNG text chunks whose keyword it does not know, such as ComfyUI's `prompt` and `workflow`. Those chunks are removed by a built-in, CRC-checked chunk filter that matches the exact keyword. ExifTool then handles EXIF and XMP.
- A1111-style parameters in JPEG/WebP `UserComment` and NovelAI-style JSON comments are classified as `ai` by content sniffing, so they are removed only with `--remove-ai`.
- **Unsupported variants:** animated WebP, APNG, HEIC/AVIF, GIF, TIFF.
- **MPF secondary images** (for example Apple's HDR gain map, or depth maps) are extracted and inspected. Without private metadata, they are kept byte-for-byte. With it, the result is `partial`.
  - ExifTool 13.x does not adjust MPF offsets when it deletes a segment between the MPF index and the image data, such as a JPEG `COM`. VeriScrub recomputes the offsets from the actual image positions. The `mpf_images_preserved` check then re-extracts every secondary image, and a mismatch means `failed`.
- **Partial:** any other JPEG data after the end-of-image marker (for example motion-photo video) is kept and not inspected.
- Face regions (`XMP-mwg-rs`, `XMP-MP`, ...) are in the `people` category and are deleted as whole structures.
- **Skipped by default:** C2PA/JUMBF (JPEG APP11, PNG `caBX`, WebP `C2PA`). Any edit invalidates the signed manifest. Override with `--allow-provenance-loss`.
- Pixels are never re-encoded.

## Video (MP4, MOV, MKV)

- FFmpeg runs with `-map_metadata -1 -map_chapters -1 -fflags +bitexact -c copy`. Retained tags (language, handler name, title, creation time unless `--also timestamp`) are re-written explicitly.
- Rotation is display-matrix side data. Stream copy preserves it, and it is verified.
- **Skipped by default:** files with subtitle, data, attachment or unknown streams, chapters, or embedded cover/thumbnail streams. The workflow keeps only audio and video, so the report lists what would be lost. Data streams (for example `gpmd`, or `tmcd` timecode) can carry GPS telemetry that is not inspected. `--allow-stream-loss` drops these items and reports each one.
- Matroska always writes a bare `encoder=Lavf` (no version) even with `+bitexact`. It is reported under `added`.
- Encoder strings inside codec bitstreams (for example x264 SEI user data) are part of the compressed stream, not container metadata, and are not removed.
- WebM, 3GP, AVI and MPEG-TS are not in the matrix (`unsupported`).
- **Skipped by default:** a top-level C2PA `uuid` box.

## Audio (MP3, M4A, FLAC)

- Tags are removed by remuxing with stream copy. Decoded audio is untouched, and the packet hash proves it.
- MP3: ID3v1 is never written. ID3v2.3 is written only if retained tags or artwork remain. FFmpeg keeps or writes a Xing/LAME info frame, which is needed for accurate duration and gapless playback and may name the encoder library.
- **Artwork:** kept byte-for-byte by default and reported. The embedded picture is checked for its own EXIF/XMP. If it has some, the result is `partial` with an explanation. Use `--remove-artwork` to drop it.
- Lyrics, chapters (M4B), ReplayGain values and MusicBrainz IDs follow their categories (`comment`, `identifier`, `other`).

## PDF

- pikepdf/qpdf always performs a **full rewrite**. Incremental updates leave earlier revisions (with old author names) in the file, and the rewrite discards them. The output is scanned for every removed value in UTF-8 and UTF-16.
- Info dictionary and document-level XMP are edited selectively. PDF/A identification (`pdfaid:*`) is technical and kept.
- **Skipped, never changed:** signed PDFs (`/SigFlags`, signature fields with values, `/Perms`) and encrypted PDFs.
- **Reported, not modified (partial):** markup annotations with author names, embedded files/attachments, object-level XMP (for example on images), PieceInfo private data. Images inside a PDF keep their own embedded metadata.
- JavaScript is flagged.

## DOCX

| Area | Handled |
|---|---|
| `docProps/core.xml`: creator, lastModifiedBy, lastPrinted, revision, description, created/modified, title, subject, keywords, category | removed by category |
| `docProps/app.xml`: Company, Manager, Template, Application, AppVersion, TotalTime | removed by category; statistics kept |
| `docProps/custom.xml`: all custom properties | removed by default (`custom`) |
| comments (`word/comments.xml`), `word/people.xml` | counted, **kept** → `partial` |
| tracked changes (`w:ins`, `w:del`, `w:moveFrom`, `w:moveTo`) | counted, **kept** → `partial` |
| `word/embeddings/*`, `word/media/*`, `docProps/thumbnail.*`, `w:rsid` | warned, kept, not inspected |
| `_xmlsignatures/` | `skipped` (any change breaks the signature) |
| password-protected (OLE/CFB container) | `skipped` |
| `.docm`, `.doc`, `.xlsx`, `.pptx` | `unsupported` |

XML parts declaring a DTD or entities are rejected. ZIP member count and expanded size are limited.

## Markdown and text

- YAML front matter must open with `---` on the first line and close with `---` or `...`. Top-level keys are removed with their indented or `- ` continuation lines. TOML (`+++`) front matter is not parsed.
- Plain text has no embedded metadata. The text itself is never reviewed or rewritten by `clean`.

## Filenames, links and limits

- External tools receive absolute paths in argument arrays (FFmpeg via `file:`), so names such as `-rf.jpg` or `--help.md` are safe. On the CLI, use `--` before such names.
- On Windows, ExifTool arguments go through a UTF-8 argfile so Unicode names work.
- Directory traversal does not follow symlinks. Symlinked files and directories are reported as `skipped`, as are explicit symlink arguments.
- Image and text files are limited to 512 MiB and 64 MiB. Each tool call has a timeout (`--timeout`, minimum 600 s for remux and hash).
