"""Identify the real file type from its bytes, not from its extension."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

# kind -> extensions that are consistent with it
EXTENSIONS = {
    "jpeg": {".jpg", ".jpeg", ".jpe", ".jfif"},
    "png": {".png"},
    "webp": {".webp"},
    "mp4": {".mp4", ".m4v"},
    "mov": {".mov", ".qt"},
    "mkv": {".mkv"},
    "mp3": {".mp3"},
    "m4a": {".m4a", ".m4b"},
    "flac": {".flac"},
    "pdf": {".pdf"},
    "docx": {".docx"},
    "text": {".txt", ".text", ".log"},
    "markdown": {".md", ".markdown", ".mdown", ".mkd"},
}

TEXT_EXT = EXTENSIONS["text"] | EXTENSIONS["markdown"]

_MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"M4VP", b"mmp4", b"dash"}
_M4A_BRANDS = {b"M4A ", b"M4B ", b"M4P "}


@dataclass
class Detected:
    kind: str | None          # supported kind, or None
    label: str                # human-readable description
    ext_mismatch: bool = False


def _sniff_head(path: Path, n: int = 4096) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def detect(path: Path) -> Detected:
    try:
        head = _sniff_head(path)
    except OSError as exc:
        return Detected(None, f"unreadable ({exc.strerror or exc})")
    ext = path.suffix.lower()
    kind, label = _from_magic(path, head)
    if kind is None and label == "unknown" and ext in TEXT_EXT:
        if _looks_text(path):
            kind = "markdown" if ext in EXTENSIONS["markdown"] else "text"
            label = "Markdown" if kind == "markdown" else "plain text"
        else:
            label = "binary data with a text extension"
    mismatch = bool(kind) and ext not in EXTENSIONS.get(kind or "", set())
    return Detected(kind, label, mismatch)


def _looks_text(path: Path, limit: int = 64 << 20) -> bool:
    try:
        if path.stat().st_size > limit:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _from_magic(path: Path, h: bytes) -> tuple[str | None, str]:
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg", "JPEG image"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "PNG image"
    if len(h) >= 12 and h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp", "WebP image"
    if len(h) >= 12 and h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand == b"qt  ":
            return "mov", "QuickTime movie"
        if brand in _M4A_BRANDS:
            return "m4a", "MPEG-4 audio"
        if brand in _MP4_BRANDS:
            return "mp4", "MPEG-4 video"
        b = brand.decode("latin-1").strip()
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc", b"avif", b"avis"):
            return None, f"HEIF/AVIF image (brand '{b}')"
        return None, f"ISO-BMFF file with unverified brand '{b}'"
    if len(h) >= 8 and h[4:8] in (b"moov", b"mdat", b"wide", b"free") :
        return "mov", "QuickTime movie (no ftyp)"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        if b"webm" in h[:64]:
            return None, "WebM (Matroska subset; not in the verified matrix)"
        if b"matroska" in h[:64]:
            return "mkv", "Matroska video"
        return None, "EBML file"
    if h.startswith(b"fLaC"):
        return "flac", "FLAC audio"
    if h.startswith(b"ID3") or (
        len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0
        and ((h[1] >> 1) & 3) == 1          # layer III
        and ((h[1] >> 3) & 3) != 1          # not the reserved MPEG version
    ):
        return "mp3", "MP3 audio"
    if b"%PDF-" in h[:1024]:
        return "pdf", "PDF document"
    if h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if path.suffix.lower() in (".docx", ".xlsx", ".pptx"):
            return None, "encrypted (password-protected) Office document"
        return None, "legacy OLE/CFB document (e.g. .doc)"
    if h.startswith(b"PK\x03\x04"):
        return _zip_kind(path)
    return None, "unknown"


def _zip_kind(path: Path) -> tuple[str | None, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                ct = zf.read("[Content_Types].xml")[:200000]
            except KeyError:
                return None, "ZIP archive"
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return None, "damaged ZIP container"
    if b"wordprocessingml.document.main+xml" in ct:
        return "docx", "Word document (DOCX)"
    if b"macroEnabled" in ct:
        return None, "macro-enabled Office document (not supported)"
    if b"spreadsheetml" in ct:
        return None, "Excel workbook (not supported)"
    if b"presentationml" in ct:
        return None, "PowerPoint presentation (not supported)"
    return None, "Office Open XML package"
