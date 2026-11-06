"""Build synthetic test fixtures with known private values.

Nothing here is real personal data. Every value is a constant below, so tests
can assert that exactly these values disappear.

    python tests/fixtures/build.py OUTDIR      # writes every fixture it can build

Builders need: Pillow (images), ExifTool (EXIF/XMP/IPTC tags), FFmpeg (media),
pikepdf (encrypted PDF only). DOCX, PDF and Markdown fixtures are pure Python.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

GPS_LAT, GPS_LON = 37.7749, -122.4194
AUTHOR = "Jane Q. Example"
EDITOR = "Mallory Editor"
SERIAL = "SN-TEST-123456"
COMMENT = "Private note: meet at the blue door"
CAMERA_MAKE, CAMERA_MODEL = "FixtureCam", "FC-1000 Pro"
COMPANY = "Example Widgets Ltd"
PROMPT = "portrait of a person in a red coat, city street at dusk"
A1111_PARAMS = (f"{PROMPT}\nNegative prompt: blurry\n"
                "Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 1234567890, Model hash: abcdef1234, Model: fixture_v1")
COMFY_PROMPT = '{"3": {"class_type": "KSampler", "inputs": {"seed": 987654321, "steps": 20}}}'
COMFY_WORKFLOW = '{"nodes": [{"id": 3, "type": "KSampler", "widgets_values": [987654321]}], "version": 0.4}'
DOC_BODY = "The quarterly numbers look fine and the launch stays on schedule."
MEDIA_TITLE = "Fixture Holiday Clip"
LOCATION_ISO6709 = "+37.7749-122.4194/"


def _run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, stdin=subprocess.DEVNULL, capture_output=True)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def exiftool_write(path: Path, *tags: str) -> None:
    _run(["exiftool", "-q", "-overwrite_original", *tags, str(path.resolve())])


# ------------------------------------------------------------------ images

def _gradient(mode: str = "RGB", size=(64, 48)):
    from PIL import Image

    im = Image.new(mode, size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            # Asymmetric pattern, so a wrong rotation is detectable.
            r, g, b = (x * 4) % 256, (y * 5) % 256, 255 if x < size[0] // 4 else 30
            px[x, y] = (r, g, b, 255 if y < size[1] // 2 else 96)[: len(mode)]
    return im


def _srgb_icc() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


PRIVATE_EXIF_TAGS = [
    f"-GPSLatitude={abs(GPS_LAT)}", "-GPSLatitudeRef=N", f"-GPSLongitude={abs(GPS_LON)}", "-GPSLongitudeRef=W",
    "-GPSAltitude=12", f"-Make={CAMERA_MAKE}", f"-Model={CAMERA_MODEL}", f"-SerialNumber={SERIAL}",
    f"-Artist={AUTHOR}", f"-XMP-dc:Creator={AUTHOR}", f"-UserComment={COMMENT}", "-ImageUniqueID=0123456789abcdef0123456789abcdef",
    "-Software=FixtureEditor 2.1", "-DateTimeOriginal=2024:05:06 07:08:09", "-Copyright=(c) Fixture Rights",
]


def jpeg_private(path: Path) -> Path:
    im = _gradient("RGB")
    im.save(path, "JPEG", quality=85, icc_profile=_srgb_icc())
    exiftool_write(path, *PRIVATE_EXIF_TAGS, f"-IPTC:By-line={AUTHOR}", f"-Comment={COMMENT}", "-Orientation#=6",
                   "-XMP-iptcExt:DigitalSourceType=http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture",
                   "-XMP-photoshop:City=San Francisco")
    return path


def jpeg_a1111(path: Path) -> Path:
    _gradient("RGB").save(path, "JPEG", quality=80)
    exiftool_write(path, f"-UserComment={A1111_PARAMS}", f"-Artist={AUTHOR}")
    return path


def _png_chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def png_ai(path: Path) -> Path:
    from PIL import PngImagePlugin

    im = _gradient("RGBA")
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", A1111_PARAMS)
    info.add_text("prompt", COMFY_PROMPT, zip=True)
    info.add_itxt("workflow", COMFY_WORKFLOW, zip=True)
    info.add_text("Author", AUTHOR)
    info.add_text("Comment", COMMENT)
    info.add_text("Software", "ComfyUI")
    info.add_text("Title", "Fixture Title")
    im.save(path, "PNG", pnginfo=info, icc_profile=_srgb_icc())
    # Add a gAMA chunk after IHDR so colour chunks are exercised.
    data = path.read_bytes()
    ihdr_end = 8 + 12 + 13
    data = data[:ihdr_end] + _png_chunk(b"gAMA", struct.pack(">I", 45455)) + data[ihdr_end:]
    path.write_bytes(data)
    exiftool_write(path, f"-GPSLatitude={abs(GPS_LAT)}", "-GPSLatitudeRef=N", f"-GPSLongitude={abs(GPS_LON)}",
                   "-GPSLongitudeRef=W", f"-XMP-dc:Creator={AUTHOR}")
    return path


def webp_private(path: Path) -> Path:
    im = _gradient("RGBA")
    im.save(path, "WEBP", lossless=True, icc_profile=_srgb_icc())
    exiftool_write(path, f"-GPSLatitude={abs(GPS_LAT)}", "-GPSLatitudeRef=N", f"-GPSLongitude={abs(GPS_LON)}",
                   "-GPSLongitudeRef=W", f"-Artist={AUTHOR}", f"-Make={CAMERA_MAKE}", f"-XMP-dc:Creator={AUTHOR}",
                   "-Orientation#=3")
    return path


def webp_animated(path: Path) -> Path:
    a, b = _gradient("RGB"), _gradient("RGB").rotate(180)
    a.save(path, "WEBP", save_all=True, append_images=[b], duration=100, lossless=True)
    return path


def apng(path: Path) -> Path:
    a, b = _gradient("RGBA"), _gradient("RGBA").rotate(180)
    a.save(path, "PNG", save_all=True, append_images=[b], duration=100)
    return path


def jpeg_c2pa(path: Path) -> Path:
    """JPEG with a synthetic APP11 JUMBF box labelled c2pa (structure only, unsigned)."""
    _gradient("RGB").save(path, "JPEG", quality=80)
    exiftool_write(path, f"-Artist={AUTHOR}")
    data = path.read_bytes()
    jumbf = b"JP" + b"\x00\x01" + b"\x00\x00\x00\x01" + struct.pack(">I", 40) + b"jumb" + struct.pack(">I", 32) + \
        b"jumd" + b"c2pa\x00\x11\x00\x10\x80\x00\xaa\x008\x9bq" + b"\x03" + b"c2pa\x00"
    seg = b"\xff\xeb" + struct.pack(">H", len(jumbf) + 2) + jumbf
    path.write_bytes(data[:2] + seg + data[2:])
    return path


def gif(path: Path) -> Path:
    _gradient("RGB").convert("P").save(path, "GIF", comment=COMMENT.encode())
    return path


# ------------------------------------------------------------------ media

_FF = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
_SRC_AV = ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
_PRIVATE_META = ["-metadata", f"artist={AUTHOR}", "-metadata", f"comment={COMMENT}",
                 "-metadata", f"location={LOCATION_ISO6709}", "-metadata", f"title={MEDIA_TITLE}",
                 "-metadata", "creation_time=2024-05-06T07:08:09Z"]


def mp4_private(path: Path, rotation: int = 90) -> Path:
    base = path.with_name(path.stem + ".base.mp4")
    _run(_FF + _SRC_AV + ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest", *_PRIVATE_META,
                          "-metadata:s:a:0", "language=eng", str(base)])
    try:  # FFmpeg >= 7
        _run(_FF + ["-display_rotation:v:0", str(rotation), "-i", str(base), "-map", "0", "-c", "copy",
                    "-map_metadata", "0", str(path)])
    except subprocess.CalledProcessError:  # FFmpeg 5/6: legacy rotate tag becomes a display matrix
        _run(_FF + ["-i", str(base), "-map", "0", "-c", "copy", "-map_metadata", "0",
                    "-metadata:s:v:0", f"rotate={rotation}", str(path)])
    base.unlink()
    return path


def mov_private(path: Path) -> Path:
    _run(_FF + _SRC_AV + ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest", *_PRIVATE_META,
                          "-movflags", "use_metadata_tags",
                          "-metadata", "com.apple.quicktime.location.ISO6709=+37.7749-122.4194+010.000/",
                          "-metadata", f"com.apple.quicktime.make={CAMERA_MAKE}",
                          "-metadata", f"com.apple.quicktime.model={CAMERA_MODEL}",
                          "-metadata:s:a:0", "language=eng", str(path)])
    return path


def mkv_private(path: Path) -> Path:
    _run(_FF + _SRC_AV + ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "flac", "-shortest", *_PRIVATE_META,
                          "-metadata", f"encoded_by={AUTHOR}", str(path)])
    return path


def mkv_extras(path: Path) -> Path:
    """MKV with a subtitle stream, chapters and a font attachment."""
    work = path.parent
    srt = work / "fixture_subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nhello\n", encoding="utf-8")
    chapters = work / "fixture_chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=Intro\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\ntitle=End\n", encoding="utf-8")
    font = work / "fixture_font.ttf"
    font.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 60)
    _run(_FF + _SRC_AV + ["-i", str(srt), "-i", str(chapters), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                          "-map_chapters", "3", "-c:v", "mpeg4", "-c:a", "flac", "-c:s", "srt",
                          "-attach", str(font), "-metadata:s:t:0", "mimetype=font/ttf",
                          *_PRIVATE_META, "-shortest", str(path)])
    for f in (srt, chapters, font):
        f.unlink()
    return path


def mp4_c2pa(path: Path) -> Path:
    mp4_private(path, rotation=0)
    uuid = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
    body = uuid + b"\x00\x00\x00\x00" + b"c2pa-fixture-manifest"
    with open(path, "ab") as fh:
        fh.write(struct.pack(">I", 8 + len(body)) + b"uuid" + body)
    return path


def _cover(path: Path, with_exif: bool = False) -> Path:
    _gradient("RGB", (32, 32)).save(path, "JPEG", quality=80)
    if with_exif:
        exiftool_write(path, f"-GPSLatitude={abs(GPS_LAT)}", "-GPSLatitudeRef=N", f"-Artist={AUTHOR}")
    return path


def audio_private(path: Path, codec: str, artwork_exif: bool = False) -> Path:
    cover = _cover(path.with_name(path.stem + ".cover.jpg"), with_exif=artwork_exif)
    extra = ["-id3v2_version", "3"] if codec == "libmp3lame" else []
    _run(_FF + ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-i", str(cover), "-map", "0", "-map", "1",
                "-c:a", codec, "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata", f"artist={AUTHOR}", "-metadata", f"comment={COMMENT}",
                "-metadata", "title=Fixture Song", "-metadata", f"encoded_by={EDITOR}", *extra, str(path)])
    cover.unlink()
    return path


# ------------------------------------------------------------------ PDF

def _pdf_bytes(info_old: bytes, info_new: bytes | None, xmp_creator: str | None = AUTHOR,
               signed: bool = False) -> bytes:
    content = f"BT /F1 12 Tf 20 100 Td ({DOC_BODY}) Tj ET".encode()
    xmp = None
    if xmp_creator:
        xmp = (f'<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
               f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               f'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
               f'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
               f'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
               f'<dc:creator><rdf:Seq><rdf:li>{xmp_creator}</rdf:li></rdf:Seq></dc:creator>'
               f'<xmp:CreatorTool>FixtureWriter 3.0</xmp:CreatorTool>'
               f'<xmpMM:DocumentID>uuid:11111111-2222-3333-4444-555555555555</xmpMM:DocumentID>'
               f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Quarterly Report</rdf:li></rdf:Alt></dc:title>'
               f'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>').encode("utf-8")
    catalog = b"<< /Type /Catalog /Pages 2 0 R" + (b" /Metadata 7 0 R" if xmp else b"")
    if signed:
        catalog += b" /AcroForm << /SigFlags 3 /Fields [8 0 R] >>"
    catalog += b" >>"
    objs: list[bytes | None] = [
        catalog,
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"STREAM:" + content,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        info_old,
        (b"STREAMXMP:" + xmp) if xmp else b"<< >>",
    ]
    if signed:
        objs.append(b"<< /FT /Sig /T (Signature1) /V << /Type /Sig /Filter /Adobe.PPKLite "
                    b"/SubFilter /adbe.pkcs7.detached /Contents <00> /ByteRange [0 0 0 0] >> >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        if obj.startswith(b"STREAMXMP:"):
            body = obj[len(b"STREAMXMP:"):]
            out.write(b"%d 0 obj\n<< /Type /Metadata /Subtype /XML /Length %d >>\nstream\n" % (n, len(body)) + body +
                      b"\nendstream\nendobj\n")
        elif obj.startswith(b"STREAM:"):
            body = obj[len(b"STREAM:"):]
            out.write(b"%d 0 obj\n<< /Length %d >>\nstream\n" % (n, len(body)) + body + b"\nendstream\nendobj\n")
        else:
            out.write(b"%d 0 obj\n" % n + obj + b"\nendobj\n")
    xref = out.tell()
    size = len(objs) + 1
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size + b"".join(b"%010d 00000 n \n" % o for o in offsets))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref))
    if info_new is not None:
        # Incremental update: replace the Info dictionary, keep the old one in the file.
        o6 = out.tell()
        out.write(b"6 0 obj\n" + info_new + b"\nendobj\n")
        x2 = out.tell()
        out.write(b"xref\n6 1\n%010d 00000 n \ntrailer\n<< /Size %d /Root 1 0 R /Info 6 0 R /Prev %d >>\n"
                  b"startxref\n%d\n%%%%EOF\n" % (o6, size, xref, x2))
    return out.getvalue()


def pdf_incremental(path: Path) -> Path:
    old = (f"<< /Author ({EDITOR}) /Creator (FixtureWriter 3.0) /Producer (FixturePDF Library 9) "
           f"/Title (Quarterly Report) /Company ({COMPANY}) /CreationDate (D:20240506070809Z) >>").encode()
    new = (f"<< /Author ({AUTHOR}) /Creator (FixtureWriter 3.0) /Producer (FixturePDF Library 9) "
           f"/Title (Quarterly Report) /Company ({COMPANY}) /CreationDate (D:20240506070809Z) >>").encode()
    path.write_bytes(_pdf_bytes(old, new))
    return path


def pdf_signed(path: Path) -> Path:
    path.write_bytes(_pdf_bytes(f"<< /Author ({AUTHOR}) >>".encode(), None, signed=True))
    return path


def pdf_encrypted(path: Path) -> Path:
    import pikepdf

    plain = path.with_name("plain-for-encryption.pdf")
    plain.write_bytes(_pdf_bytes(f"<< /Author ({AUTHOR}) >>".encode(), None))
    with pikepdf.open(plain) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user="fixture-user", owner="fixture-owner"))
    plain.unlink()
    return path


# ------------------------------------------------------------------ DOCX

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _document(comments: bool, tracked: bool) -> str:
    extra = ""
    if tracked:
        extra = (f'<w:p><w:ins w:id="1" w:author="{EDITOR}" w:date="2024-05-06T07:08:09Z"><w:r><w:t>Inserted text.</w:t>'
                 f'</w:r></w:ins></w:p>')
    cref = ('<w:commentRangeStart w:id="0"/><w:r><w:t>Commented.</w:t></w:r><w:commentRangeEnd w:id="0"/>'
            '<w:r><w:commentReference w:id="0"/></w:r>') if comments else ""
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {_W}><w:body>'
            f'<w:p><w:r><w:t>{DOC_BODY}</w:t></w:r></w:p><w:p>{cref}</w:p>{extra}<w:sectPr/></w:body></w:document>')


def docx_private(path: Path, comments: bool = True, tracked: bool = True, signed: bool = False) -> Path:
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Quarterly Report</dc:title><dc:subject>Finance</dc:subject><dc:creator>{AUTHOR}</dc:creator><cp:keywords>q2, plan</cp:keywords><dc:description>{COMMENT}</dc:description><cp:lastModifiedBy>{EDITOR}</cp:lastModifiedBy><cp:revision>17</cp:revision><cp:lastPrinted>2024-05-01T10:00:00Z</cp:lastPrinted><dcterms:created xsi:type="dcterms:W3CDTF">2024-05-06T07:08:09Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2024-05-07T07:08:09Z</dcterms:modified></cp:coreProperties>"""
    app = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Template>Normal.dotm</Template><TotalTime>42</TotalTime><Pages>1</Pages><Words>11</Words><Application>Microsoft Office Word</Application><DocSecurity>0</DocSecurity><Company>{COMPANY}</Company><Manager>{EDITOR}</Manager><AppVersion>16.0000</AppVersion></Properties>"""
    custom = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="ClientMatter"><vt:lpwstr>ACME-2024-0042</vt:lpwstr></property><property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="3" name="Reviewer"><vt:lpwstr>{EDITOR}</vt:lpwstr></property></Properties>"""
    comments_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:comments {_W}>'
                    f'<w:comment w:id="0" w:author="{EDITOR}" w:date="2024-05-06T07:08:09Z" w:initials="ME">'
                    f'<w:p><w:r><w:t>Check this number.</w:t></w:r></w:p></w:comment></w:comments>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CT)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", _document(comments, tracked))
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        if comments:
            zf.writestr("word/comments.xml", comments_xml)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
        zf.writestr("docProps/custom.xml", custom)
        if signed:
            zf.writestr("_xmlsignatures/sig1.xml", "<Signature/>")
    return path


def docx_encrypted(path: Path) -> Path:
    # Password-protected OOXML is an OLE compound file, not a ZIP.
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504)
    return path


# ------------------------------------------------------------------ text

MARKDOWN = (
    "---\n"
    f"title: Launch notes\n"
    f"author: {AUTHOR}\n"
    "email: jane@example.invalid\n"
    "tags:\n"
    "- launch\n"
    "- internal\n"
    "draft: true\n"
    "---\n"
    "# Launch notes\n\n"
    "author: this line is body text and must survive.\n\n"
    f"{DOC_BODY}\n"
)


def markdown(path: Path, crlf: bool = False) -> Path:
    text = MARKDOWN.replace("\n", "\r\n") if crlf else MARKDOWN
    path.write_bytes(text.encode("utf-8"))
    return path


def text_file(path: Path) -> Path:
    path.write_text(f"{DOC_BODY}\nSigned, {AUTHOR}\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ malformed

def malformed(dirpath: Path) -> dict[str, Path]:
    out = {}
    jp = dirpath / "truncated.jpg"
    jp.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x43")
    out["jpeg"] = jp
    png = dirpath / "badcrc.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00")
    out["png"] = png
    mp4 = dirpath / "garbage.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x13\x37" * 200)
    out["mp4"] = mp4
    pdf = dirpath / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog >>\n%%EOF garbage")
    out["pdf"] = pdf
    docx = dirpath / "notaword.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", _CT)
        zf.writestr("word/document.xml", "<w:document><unclosed>")
    out["docx"] = docx
    return out


# ------------------------------------------------------------------ CLI

def build_all(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    made.append(docx_private(outdir / "report.docx"))
    made.append(pdf_incremental(outdir / "report.pdf"))
    made.append(markdown(outdir / "notes.md"))
    made.append(text_file(outdir / "letter.txt"))
    try:
        import PIL  # noqa: F401
        pil = True
    except ImportError:
        pil = False
    if pil and have("exiftool"):
        made += [jpeg_private(outdir / "photo.jpg"), png_ai(outdir / "generated.png"),
                 webp_private(outdir / "photo.webp")]
    if have("ffmpeg"):
        made += [mp4_private(outdir / "clip.mp4"), mov_private(outdir / "clip.mov"), mkv_private(outdir / "clip.mkv")]
        if pil:
            made += [audio_private(outdir / "song.mp3", "libmp3lame"), audio_private(outdir / "song.flac", "flac"),
                     audio_private(outdir / "song.m4a", "aac")]
    return made


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures-out")
    for p in build_all(target):
        print(p)
