"""Tool-independent tests: policy, detection, safe publishing, CLI basics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import run_main, run_subprocess
from veriscrub.detect import detect
from veriscrub.models import Field, safe_text
from veriscrub.policy import Policy, classify_av, classify_exiftool, classify_png_text, looks_like_ai
from veriscrub.safeio import publish, scan_for_values


def test_default_policy_and_ai_opt_in():
    p = Policy.build()
    assert {"location", "device", "author", "comment"} <= p.remove
    assert "ai" not in p.remove and "software" not in p.remove
    assert {"ai", "software"} <= Policy.build(remove_ai=True).remove
    assert "timestamp" in Policy.build(also=["timestamp,descriptive"]).remove
    assert not ({"technical", "disclosure", "provenance"} & Policy.build(everything=True).remove)
    with pytest.raises(ValueError):
        Policy.build(also=["technical"])
    with pytest.raises(ValueError):
        Policy.build(also=["nonsense"])


@pytest.mark.parametrize("g0,g1,tag,cat", [
    ("EXIF", "GPS", "GPSLatitude", "location"),
    ("EXIF", "IFD0", "Make", "device"),
    ("EXIF", "ExifIFD", "SerialNumber", "device"),
    ("EXIF", "IFD0", "Artist", "author"),
    ("EXIF", "IFD0", "Orientation", "technical"),
    ("EXIF", "ExifIFD", "ExposureTime", "capture"),
    ("EXIF", "ExifIFD", "DateTimeOriginal", "timestamp"),
    ("XMP", "XMP-iptcExt", "DigitalSourceType", "disclosure"),
    ("XMP", "XMP-dc", "Creator", "author"),
    ("XMP", "XMP-xmpMM", "DocumentID", "identifier"),
    ("MakerNotes", "Canon", "InternalSerialNumber", "device"),
    ("EXIF", "IFD1", "ThumbnailImage", "preview"),
])
def test_exiftool_classification(g0, g1, tag, cat):
    assert classify_exiftool(g0, g1, tag, "x") == cat


def test_ungrouped_and_system_keys_ignored():
    assert classify_exiftool("", "", "SourceFile", "/home/user/a.jpg") is None
    assert classify_exiftool("System", "System", "FileName", "a.jpg") is None


def test_ai_content_sniffing():
    assert looks_like_ai("x\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 1")
    assert classify_exiftool("EXIF", "ExifIFD", "UserComment", "a cat\nSteps: 20, Seed: 42") == "ai"
    assert classify_exiftool("EXIF", "ExifIFD", "UserComment", "meet at noon") == "comment"
    assert classify_png_text("parameters", "anything") == "ai"
    assert classify_png_text("workflow", "{}") == "ai"
    assert classify_png_text("Author", "J") == "author"


@pytest.mark.parametrize("key,cat", [
    ("location", "location"), ("location-eng", "location"),
    ("com.apple.quicktime.location.ISO6709", "location"), ("com.apple.quicktime.make", "device"),
    ("com.android.model", "device"), ("artist", "author"), ("comment", "comment"),
    ("encoder", "software"), ("creation_time", "timestamp"), ("title", "descriptive"),
    ("language", "technical"), ("handler_name", "technical"), ("major_brand", "technical"),
    ("ENCODED_BY", "author"), ("DURATION", "technical"),
])
def test_av_classification(key, cat):
    assert classify_av(key, "v") == cat


def test_safe_text_escapes_terminal_controls():
    s = safe_text("ok\x1b[31mred\x07\nnext")
    assert "\x1b" not in s and "\x07" not in s and "\n" not in s
    assert "\\x1b" in s


def test_field_values_redacted_by_default():
    f = Field("EXIF:IFD0:Artist", "author", "Jane")
    assert f.to_dict()["value"] == "<redacted 4 chars>"
    assert f.to_dict(show_values=True)["value"] == "Jane"


def test_detect_uses_content_not_extension(tmp_path):
    p = tmp_path / "-tricky name ü.txt"
    p.write_bytes(b"%PDF-1.7\n")
    d = detect(p)
    assert d.kind == "pdf" and d.ext_mismatch
    q = tmp_path / "notes.md"
    q.write_text("# hi\n", encoding="utf-8")
    assert detect(q).kind == "markdown"
    b = tmp_path / "binary.txt"
    b.write_bytes(b"\x00\x01\x02")
    assert detect(b).kind is None


def test_publish_never_overwrites(tmp_path):
    dest = tmp_path / "out.jpg"
    dest.write_bytes(b"existing")
    tmp = tmp_path / "t1"
    tmp.write_bytes(b"new")
    final = publish(tmp, dest)
    assert final == tmp_path / "out-1.jpg"
    assert dest.read_bytes() == b"existing" and final.read_bytes() == b"new"
    tmp2 = tmp_path / "t2"
    tmp2.write_bytes(b"x")
    assert publish(tmp2, dest, on_collision="skip") is None
    with pytest.raises(FileExistsError):
        publish(tmp2, dest, on_collision="fail")
    assert dest.read_bytes() == b"existing"


def test_publish_reserved_names_within_batch(tmp_path):
    reserved: set[str] = set()
    outs = []
    for i in range(3):
        t = tmp_path / f"t{i}"
        t.write_bytes(str(i).encode())
        outs.append(publish(t, tmp_path / "same.png", reserved=reserved))
    assert [o.name for o in outs] == ["same.png", "same-1.png", "same-2.png"]


def test_scan_for_values_crosses_chunk_boundaries(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"a" * 1000 + b"SECRET-VALUE" + b"b" * 1000)
    assert scan_for_values(p, [b"SECRET-VALUE", b"absent"], chunk=1005) == {b"SECRET-VALUE"}


def test_unknown_format_unsupported(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VERISCRUB_EXIFTOOL", "")
    p = tmp_path / "blob.xyz"
    p.write_bytes(os.urandom(64))
    code, doc = run_main(["inspect", str(p), "--json"], capsys)
    assert doc["results"][0]["status"] == "unsupported" and code == 3
    code, doc = run_main(["clean", str(p), "--json", "--output-dir", str(tmp_path / "o")], capsys)
    assert doc["results"][0]["status"] == "unsupported"
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())


def test_directory_clean_requires_output_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    code, out = run_subprocess(["clean", str(tmp_path)])
    assert code == 2 and "--output-dir" in out


def test_doctor_json_runs(capsys):
    code, doc = run_main(["doctor", "--json"], capsys)
    names = {c["name"] for c in doc["checks"]}
    assert {"python", "exiftool", "ffmpeg", "ffprobe", "pikepdf"} <= names
    assert code in (0, 3)


def test_doctor_reports_missing_tools(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("VERISCRUB_EXIFTOOL", str(tmp_path / "no-such-exiftool"))
    monkeypatch.setenv("VERISCRUB_FFMPEG", str(tmp_path / "no-such-ffmpeg"))
    code, doc = run_main(["doctor", "--json"], capsys)
    by = {c["name"]: c for c in doc["checks"]}
    assert not by["exiftool"]["ok"] and "not found" in by["exiftool"]["detail"]
    assert not by["ffmpeg"]["ok"]
    assert doc["formats"]["JPEG / PNG / static WebP"] is False
    assert code == 3


def test_symlinks_not_followed(tmp_path, capsys):
    src = tmp_path / "in"
    src.mkdir()
    real = tmp_path / "outside.md"
    real.write_text("---\nauthor: x\n---\nbody\n", encoding="utf-8")
    try:
        (src / "link.md").symlink_to(real)
        (src / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json",
                          "--front-matter-key", "author"], capsys)
    statuses = {Path(r["path"]).name: (r["status"], r.get("reason", "")) for r in doc["results"]}
    assert statuses["link.md"][0] == "skipped" and "symbolic link" in statuses["link.md"][1]
    assert statuses["linkdir"][0] == "skipped"
    assert not (tmp_path / "o" / "link.md").exists()


def test_missing_backend_for_jpeg_is_unsupported(tmp_path, capsys, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "p.jpg"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(src, "JPEG")
    monkeypatch.setenv("VERISCRUB_EXIFTOOL", str(tmp_path / "missing-exiftool"))
    for cmd in ("inspect", "clean"):
        args = [cmd, str(src), "--json"] + (["--output-dir", str(tmp_path / "o")] if cmd == "clean" else [])
        code, doc = run_main(args, capsys)
        assert doc["results"][0]["status"] == "unsupported" and "exiftool" in doc["results"][0]["reason"]
        assert code == 3
    assert not (tmp_path / "o").exists()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_unreadable_file_is_failed_not_inspected(tmp_path, capsys):
    p = tmp_path / "locked.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    p.chmod(0)
    try:
        for args in (["inspect", str(p)], ["clean", str(p), "--output-dir", str(tmp_path / "o")], ["verify", str(p)]):
            code, doc = run_main(args + ["--json"], capsys)
            r = doc["results"][0]
            assert r["status"] == "failed" and "cannot read the file" in r["reason"], r
            assert code == 1
    finally:
        p.chmod(0o644)
