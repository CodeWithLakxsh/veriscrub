# Metadata, provenance, watermarks and detectors are different things

| Layer | Where it lives | What this tool does |
|---|---|---|
| **Container metadata**: EXIF, XMP, IPTC, PNG text, ID3, MP4/MKV tags, PDF Info, DOCX properties | Labelled fields beside the content | Inspects it, removes the selected categories, and verifies their absence after re-opening |
| **Signed provenance**: C2PA / Content Credentials (JUMBF in JPEG APP11, PNG `caBX`, WebP `C2PA`, MP4 `uuid` box) | A cryptographically signed manifest bound to the content hash | Detects it structurally, flags it, and **skips the file by default**. Any edit invalidates the signature. Signatures are not validated. |
| **AI-disclosure labels**: IPTC `DigitalSourceType` | A metadata field | Reported and always kept. It is a disclosure, not private data. |
| **Embedded watermarks**: invisible pixel- or audio-level marks | Inside the pixels or samples | Not detected, not removed. Pixels and samples are never re-encoded, so any watermark is untouched. |
| **Detector scores**: classifiers that guess whether content is AI-generated | Outside the file | Not affected and not measured |

## What to tell users

- Say: "These metadata fields were removed and their absence was verified by re-reading the file."
- Never say that a file is human-made, authentic, undetectable, unwatermarked, "clean of AI traces" or free of hidden signals. The tool cannot know any of that.
- If a file has Content Credentials, explain the trade-off before using `--allow-provenance-loss`. The manifest becomes invalid, but it may still be present and readable.
- Removing AI generation parameters (prompts, seeds, workflows) is a privacy measure, because prompts can contain names, places or other personal details. It does not change what the content is.
