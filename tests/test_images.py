"""JPEG / PNG / WebP via ExifTool, verified by re-opening outputs with ExifTool and Pillow."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from conftest import keys, needs_exiftool, result, run_main
from fixtures import build

pytestmark = needs_exiftool


def exif(path: Path) -> dict:
    out = subprocess.run(["exiftool", "-j", "-a", "-G1", "-n", str(path)], capture_output=True, check=True).stdout
    return json.loads(out)[0]


def pixels(path: Path, transpose: bool = False) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        if transpose:
            im = ImageOps.exif_transpose(im)
        return im.convert("RGBA").tobytes()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_jpeg_private_fields_removed_after_reopen(tmp_path, capsys):
    src = build.jpeg_private(tmp_path / "photo.jpg")
    before = sha(src)
    out = tmp_path / "clean.jpg"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean" and code == 0, r
    meta = exif(out)
    assert not [k for k in meta if k.startswith("GPS:")]
    for tag in ("IFD0:Make", "IFD0:Model", "ExifIFD:SerialNumber", "IFD0:Artist", "XMP-dc:Creator",
                "IPTC:By-line", "File:Comment", "ExifIFD:UserComment", "ExifIFD:ImageUniqueID", "XMP-photoshop:City"):
        assert tag not in meta, tag
    blob = out.read_bytes()
    for secret in (build.AUTHOR, build.SERIAL, build.COMMENT, build.CAMERA_MODEL, "San Francisco"):
        assert secret.encode() not in blob, secret
    # Retained by default: orientation, ICC profile, disclosure label, rights, timestamps, software.
    assert meta["IFD0:Orientation"] == 6
    assert "ICC_Profile:ProfileDescription" in meta or "ICC-header:ProfileVersion" in meta or \
        any(k.startswith("ICC") for k in meta)
    assert "XMP-iptcExt:DigitalSourceType" in meta
    assert meta.get("IFD0:Copyright") and meta.get("ExifIFD:DateTimeOriginal")
    # Pixels and displayed orientation identical; original untouched.
    assert pixels(out) == pixels(src)
    assert pixels(out, transpose=True) == pixels(src, transpose=True)
    assert sha(src) == before
    for check in ("image_data_unchanged", "orientation_preserved", "color_profile_preserved", "original_unchanged"):
        assert r["checks"][check]["ok"], check


def test_jpeg_optional_categories(tmp_path, capsys):
    src = build.jpeg_private(tmp_path / "photo.jpg")
    out = tmp_path / "all.jpg"
    run_main(["clean", str(src), "-o", str(out), "--all", "--json"], capsys)
    meta = exif(out)
    for tag in ("IFD0:Software", "IFD0:Copyright", "ExifIFD:DateTimeOriginal"):
        assert tag not in meta, tag
    assert meta["IFD0:Orientation"] == 6 and "XMP-iptcExt:DigitalSourceType" in meta


def test_png_ai_fields_need_explicit_flag(tmp_path, capsys):
    src = build.png_ai(tmp_path / "gen.png")
    default_out = tmp_path / "default.png"
    code, doc = run_main(["clean", str(src), "-o", str(default_out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean"
    assert {"PNG-text:Author", "PNG-text:Comment", "XMP:XMP-dc:Creator"} <= keys(r["removed"])
    data = default_out.read_bytes()
    assert b"parameters" in data and b"workflow" in data  # AI fields kept without --remove-ai
    assert build.AUTHOR.encode() not in data and b"GPS" not in json.dumps(exif(default_out)).encode()

    ai_out = tmp_path / "noai.png"
    code, doc = run_main(["clean", str(src), "-o", str(ai_out), "--remove-ai", "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    assert {"PNG-text:parameters", "PNG-text:prompt", "PNG-text:workflow", "PNG-text:Software"} <= keys(r["removed"])
    from veriscrub.containers import parse_png

    st = parse_png(ai_out.read_bytes())
    assert [k for _t, k, _v in st.texts if k != "XML:com.adobe.xmp"] == ["Title"]
    raw = ai_out.read_bytes()
    for secret in ("1234567890", "987654321", "KSampler", build.PROMPT):
        assert secret.encode() not in raw
    # Transparency, colour profile and gamma preserved; pixels identical.
    assert pixels(ai_out) == pixels(src)
    orig_st = parse_png(src.read_bytes())
    assert st.icc_hash == orig_st.icc_hash and st.icc_hash and st.has_alpha
    assert b"gAMA" in raw


def test_png_inspect_reports_ai_fields_redacted(tmp_path, capsys):
    src = build.png_ai(tmp_path / "gen.png")
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    fields = {f["key"]: f for f in result(doc)["fields"]}
    assert fields["PNG-text:parameters"]["category"] == "ai"
    assert fields["PNG-text:workflow"]["category"] == "ai"
    text = json.dumps(doc)
    assert "1234567890" not in text and build.AUTHOR not in text  # values redacted by default
    code, doc = run_main(["inspect", str(src), "--json", "--show-values"], capsys)
    assert build.AUTHOR in json.dumps(doc)


def test_jpeg_a1111_parameters_in_usercomment_are_ai(tmp_path, capsys):
    src = build.jpeg_a1111(tmp_path / "a.jpg")
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    cats = {f["key"]: f["category"] for f in result(doc)["fields"]}
    assert cats["EXIF:ExifIFD:UserComment"] == "ai"
    out = tmp_path / "c.jpg"
    run_main(["clean", str(src), "-o", str(out), "--remove-ai", "--json"], capsys)
    assert b"Seed: 1234567890" not in out.read_bytes()


def test_webp_static(tmp_path, capsys):
    src = build.webp_private(tmp_path / "p.webp")
    out = tmp_path / "c.webp"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    meta = exif(out)
    assert not [k for k in meta if k.startswith("GPS:")] and "IFD0:Artist" not in meta and "IFD0:Make" not in meta
    assert meta["IFD0:Orientation"] == 3
    assert pixels(out) == pixels(src)
    assert r["checks"]["transparency_preserved"]["ok"] and r["checks"]["color_profile_preserved"]["ok"]


@pytest.mark.parametrize("maker", [build.webp_animated, build.apng])
def test_animated_variants_unsupported(tmp_path, capsys, maker):
    src = maker(tmp_path / ("anim.webp" if maker is build.webp_animated else "anim.png"))
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "unsupported" and "animated" in r["reason"]
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())


def test_c2pa_skipped_by_default(tmp_path, capsys):
    src = build.jpeg_c2pa(tmp_path / "cr.jpg")
    before = sha(src)
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "skipped" and "C2PA" in r["reason"] and "provenance:c2pa" in r["flags"]
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--allow-provenance-loss",
                          "--json"], capsys)
    assert result(doc)["status"] in ("clean", "partial")
    assert sha(src) == before


def test_unknown_format_read_only_inspect(tmp_path, capsys):
    src = build.gif(tmp_path / "a.gif")
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "inspected" and "read-only" in r["flags"] and "read-only" in r["reason"]
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "unsupported"


@pytest.mark.parametrize("kind", ["jpeg", "png"])
def test_malformed_images_fail(tmp_path, capsys, kind):
    bad = build.malformed(tmp_path)[kind]
    code, doc = run_main(["clean", str(bad), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "failed" and code == 1
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())


def test_missing_exiftool_is_reported(tmp_path, capsys, monkeypatch):
    src = build.jpeg_private(tmp_path / "p.jpg")
    monkeypatch.setenv("VERISCRUB_EXIFTOOL", str(tmp_path / "nope"))
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "unsupported" and "exiftool" in r["reason"] and code == 3


def test_iphone_style_mpf_secondary_inspected_and_kept(tmp_path, capsys):
    src = build.jpeg_mpf(tmp_path / "iphone.jpg")
    secondary = src.read_bytes()[-2000:]
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    r = result(doc)
    assert "jpeg:mpf-secondary:1" in r["flags"] and not r.get("unhandled"), r
    out = tmp_path / "c.jpg"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    assert out.read_bytes().endswith(secondary)  # secondary image kept byte-for-byte
    assert subprocess.run(["exiftool", "-b", "-MPImage2", str(out)], capture_output=True).stdout.endswith(secondary)
    assert not [k for k in exif(out) if k.startswith("GPS:")]


def test_mpf_secondary_with_private_metadata_is_partial(tmp_path, capsys):
    src = build.jpeg_mpf(tmp_path / "p.jpg", secondary_private=True)
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "partial" and any("carry their own metadata" in u for u in r["unhandled"]), r


def test_unknown_jpeg_trailer_is_partial(tmp_path, capsys):
    src = build.jpeg_mpf(tmp_path / "m.jpg", junk_trailer=True)
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "partial" and any("not inspected" in u for u in r["unhandled"]), r


def test_face_regions_removed_by_default(tmp_path, capsys):
    src = build.jpeg_private(tmp_path / "f.jpg")
    build.exiftool_write(src, "-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=64,H=48,Unit=pixel},"
                              "RegionList=[{Area={X=0.5,Y=0.5,W=0.2,H=0.2,Unit=normalized},Type=Face,Name=Jane}]}")
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    cats = {f["key"]: f["category"] for f in result(doc)["fields"]}
    assert cats.get("XMP:XMP-mwg-rs:RegionName") == "people"
    out = tmp_path / "c.jpg"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    assert result(doc)["status"] == "clean"
    assert not [k for k in exif(out) if k.startswith("XMP-mwg-rs:")]
