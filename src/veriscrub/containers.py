"""Small, bounds-checked parsers for JPEG, PNG, WebP and ISO-BMFF structure.

These are used as an *independent* check alongside ExifTool:

* hash the image data (everything except metadata segments) so a cleaned copy
  can be proven to contain the same pixels, colour profile and transparency;
* detect variants outside the verified matrix (APNG, animated WebP, JPEG
  trailers) and signed provenance (C2PA / JUMBF);
* read PNG text chunks with their exact keywords, and remove selected ones
  (ExifTool cannot delete unregistered PNG keywords such as ``workflow``).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field

MAX_TEXT = 16 << 20  # decompressed PNG text chunk limit


class MalformedError(ValueError):
    pass


@dataclass
class ImageStructure:
    kind: str
    image_hash: str = ""
    icc_hash: str = ""
    has_alpha: bool | None = None
    c2pa: bool = False
    animated: bool = False
    trailer_bytes: int = 0
    mpf: bool = False
    texts: list[tuple[str, str, str]] = field(default_factory=list)  # (chunk type, keyword, value)
    has_time_chunk: bool = False
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- JPEG

def parse_jpeg(data: bytes) -> ImageStructure:
    if not data.startswith(b"\xff\xd8"):
        raise MalformedError("not a JPEG (missing SOI)")
    st = ImageStructure("jpeg")
    img = hashlib.sha256()
    icc = hashlib.sha256()
    has_icc = False
    i = 2
    n = len(data)
    eoi = None
    while i < n:
        if data[i] != 0xFF:
            raise MalformedError(f"JPEG marker expected at offset {i}")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            raise MalformedError("truncated JPEG marker")
        marker = data[i]
        i += 1
        if marker == 0xD9:  # EOI
            img.update(b"\xff\xd9")
            eoi = i
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            img.update(bytes([0xFF, marker]))
            continue
        if i + 2 > n:
            raise MalformedError("truncated JPEG segment length")
        seglen = struct.unpack(">H", data[i:i + 2])[0]
        if seglen < 2 or i + seglen > n:
            raise MalformedError(f"JPEG segment 0x{marker:02X} overruns the file")
        payload = data[i + 2:i + seglen]
        start = i - 2
        i += seglen
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            if marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
                icc.update(payload[14:])
                has_icc = True
            elif marker == 0xE2 and payload.startswith(b"MPF\x00"):
                st.mpf = True
            elif marker == 0xEB and b"jumb" in payload[:64] and (b"c2pa" in payload or b"jumd" in payload):
                st.c2pa = True
            continue
        img.update(data[start:i])
        if marker == 0xDA:  # SOS: entropy-coded data follows
            j = i
            while j < n - 1:
                if data[j] == 0xFF and data[j + 1] != 0x00 and not (0xD0 <= data[j + 1] <= 0xD7) and data[j + 1] != 0xFF:
                    break
                j += 1
            else:
                raise MalformedError("JPEG scan data has no terminating marker")
            img.update(data[i:j])
            i = j
    if eoi is None:
        raise MalformedError("JPEG has no end-of-image marker")
    trailer = data[eoi:]
    if trailer.strip(b"\x00"):
        st.trailer_bytes = len(trailer)
        img.update(trailer)
    st.image_hash = img.hexdigest()
    st.icc_hash = icc.hexdigest() if has_icc else ""
    return st


# ----------------------------------------------------------------- PNG

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_TEXT = {b"tEXt", b"zTXt", b"iTXt"}
PNG_META = PNG_TEXT | {b"eXIf", b"tIME"}
XMP_KEYWORD = "XML:com.adobe.xmp"


def _png_chunks(data: bytes):
    if not data.startswith(PNG_SIG):
        raise MalformedError("not a PNG (bad signature)")
    i = 8
    n = len(data)
    seen_iend = False
    while i < n:
        if i + 8 > n:
            raise MalformedError("truncated PNG chunk header")
        length, ctype = struct.unpack(">I4s", data[i:i + 8])
        end = i + 12 + length
        if length > 0x7FFFFFFF or end > n:
            raise MalformedError(f"PNG chunk {ctype!r} overruns the file")
        body = data[i + 8:i + 8 + length]
        crc = struct.unpack(">I", data[i + 8 + length:end])[0]
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise MalformedError(f"PNG chunk {ctype!r} has a bad CRC")
        yield ctype, body, data[i:end]
        i = end
        if ctype == b"IEND":
            seen_iend = True
            break
    if not seen_iend:
        raise MalformedError("PNG has no IEND chunk")


def _inflate(blob: bytes) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(blob, MAX_TEXT)
    if d.unconsumed_tail:
        raise MalformedError("PNG text chunk exceeds the decompression limit")
    return out


def _png_text(ctype: bytes, body: bytes) -> tuple[str, str]:
    key, sep, rest = body.partition(b"\x00")
    if not sep:
        raise MalformedError("PNG text chunk without keyword terminator")
    keyword = key.decode("latin-1")
    if ctype == b"tEXt":
        return keyword, rest.decode("latin-1")
    if ctype == b"zTXt":
        return keyword, _inflate(rest[1:]).decode("latin-1", "replace")
    # iTXt
    if len(rest) < 2:
        raise MalformedError("short iTXt chunk")
    comp_flag = rest[0]
    rest = rest[2:]
    _lang, _, rest = rest.partition(b"\x00")
    _tkey, _, text = rest.partition(b"\x00")
    if comp_flag:
        text = _inflate(text)
    return keyword, text.decode("utf-8", "replace")


def parse_png(data: bytes) -> ImageStructure:
    st = ImageStructure("png")
    img = hashlib.sha256()
    icc = ""
    for ctype, body, raw in _png_chunks(data):
        if ctype in PNG_TEXT:
            keyword, value = _png_text(ctype, body)
            st.texts.append((ctype.decode(), keyword, value))
            continue
        if ctype == b"tIME":
            st.has_time_chunk = True
            continue
        if ctype == b"eXIf":
            continue
        if ctype == b"caBX":
            st.c2pa = True
            continue
        if ctype in (b"acTL", b"fcTL", b"fdAT"):
            st.animated = True
        if ctype == b"iCCP":
            icc = hashlib.sha256(body).hexdigest()
        if ctype == b"IHDR" and len(body) >= 10:
            st.has_alpha = body[9] in (4, 6)
        if ctype == b"tRNS":
            st.has_alpha = True
        img.update(raw)
    st.image_hash = img.hexdigest()
    st.icc_hash = icc
    return st


def png_remove_text(data: bytes, keywords: set[str], drop_time: bool = False) -> tuple[bytes, list[str]]:
    """Return PNG bytes without text chunks whose keyword is in ``keywords``."""
    out = [PNG_SIG]
    removed: list[str] = []
    for ctype, body, raw in _png_chunks(data):
        if ctype in PNG_TEXT:
            keyword, _ = _png_text(ctype, body)
            if keyword in keywords and keyword != XMP_KEYWORD:
                removed.append(keyword)
                continue
        if ctype == b"tIME" and drop_time:
            removed.append("tIME")
            continue
        out.append(raw)
    return b"".join(out), removed


# ----------------------------------------------------------------- WebP

def parse_webp(data: bytes) -> ImageStructure:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise MalformedError("not a WebP file")
    riff_size = struct.unpack("<I", data[4:8])[0]
    if riff_size + 8 > len(data) or riff_size < 4:
        raise MalformedError("WebP RIFF size exceeds the file")
    st = ImageStructure("webp")
    img = hashlib.sha256()
    i = 12
    end = 8 + riff_size
    have_image = False
    while i < end:
        if i + 8 > end:
            raise MalformedError("truncated WebP chunk header")
        fourcc = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        body_end = i + 8 + size
        if body_end > end:
            raise MalformedError(f"WebP chunk {fourcc!r} overruns the file")
        body = data[i + 8:body_end]
        if fourcc == b"VP8X":
            if size < 10:
                raise MalformedError("short VP8X chunk")
            flags = body[0]
            st.animated = st.animated or bool(flags & 0x02)
            st.has_alpha = bool(flags & 0x10)
            img.update(b"canvas" + body[4:10])
        elif fourcc in (b"ANIM", b"ANMF"):
            st.animated = True
            img.update(fourcc + body)
        elif fourcc == b"ICCP":
            st.icc_hash = hashlib.sha256(body).hexdigest()
        elif fourcc in (b"EXIF", b"XMP "):
            pass
        elif fourcc in (b"C2PA",):
            st.c2pa = True
        else:
            if fourcc in (b"VP8 ", b"VP8L"):
                have_image = True
                if fourcc == b"VP8L" and st.has_alpha is None and len(body) >= 5:
                    st.has_alpha = bool((body[4] >> 4) & 1)
            img.update(fourcc + body)
        i = body_end + (size & 1)
    if not have_image and not st.animated:
        raise MalformedError("WebP has no image bitstream")
    st.image_hash = img.hexdigest()
    return st


def parse_image(kind: str, data: bytes) -> ImageStructure:
    if kind == "jpeg":
        return parse_jpeg(data)
    if kind == "png":
        return parse_png(data)
    if kind == "webp":
        return parse_webp(data)
    raise ValueError(kind)


# ----------------------------------------------------------------- ISO-BMFF

C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")


def bmff_top_level_c2pa(path, max_boxes: int = 100000) -> bool:
    """True if a top-level ``uuid`` box carries the C2PA manifest UUID."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size_total = fh.tell()
        pos = 0
        for _ in range(max_boxes):
            if pos + 8 > size_total:
                return False
            fh.seek(pos)
            hdr = fh.read(8)
            size, btype = struct.unpack(">I4s", hdr)
            header = 8
            if size == 1:
                big = fh.read(8)
                if len(big) < 8:
                    return False
                size = struct.unpack(">Q", big)[0]
                header = 16
            elif size == 0:
                size = size_total - pos
            if size < header:
                return False
            if btype == b"uuid":
                if fh.read(16) == C2PA_UUID:
                    return True
            pos += size
    return False


def jumbf_c2pa_anywhere(path) -> bool:
    """Conservative fallback: a JUMBF superbox ('jumb') and a 'c2pa' label both occur in the file."""
    from veriscrub.safeio import scan_for_values

    return scan_for_values(path, [b"jumb", b"c2pa"]) == {b"jumb", b"c2pa"}
