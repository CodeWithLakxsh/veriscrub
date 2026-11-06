"""MP4 / MOV / MKV / MP3 / M4A / FLAC via FFmpeg, verified with ffprobe after re-opening."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from conftest import keys, needs_ffmpeg, result, run_main
from fixtures import build

pytestmark = needs_ffmpeg


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          "-show_chapters", str(path)], capture_output=True, check=True).stdout
    return json.loads(out)


def all_tags(p: dict) -> dict:
    tags = dict(p["format"].get("tags", {}))
    for s in p["streams"]:
        for k, v in (s.get("tags") or {}).items():
            tags[f"s{s['index']}:{k}"] = v
    return {k.lower(): v for k, v in tags.items()}


def rotation(p: dict) -> list[float]:
    rots = []
    for s in p["streams"]:
        if s["codec_type"] == "video":
            rots.append(next((float(sd["rotation"]) for sd in s.get("side_data_list", []) if "rotation" in sd), 0.0))
    return rots


def decodes(path: Path) -> bool:
    cp = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True)
    return cp.returncode == 0 and not cp.stderr.strip()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("maker,name", [(build.mp4_private, "clip.mp4"), (build.mov_private, "clip.mov"),
                                        (build.mkv_private, "clip.mkv")])
def test_video_tags_removed_playback_preserved(tmp_path, capsys, maker, name):
    src = maker(tmp_path / name)
    before = sha(src)
    out = tmp_path / ("clean-" + name)
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    po, pn = probe(src), probe(out)
    tags = all_tags(pn)
    for k in tags:
        assert "location" not in k and "artist" not in k and "comment" not in k and "make" not in k
    raw = out.read_bytes()
    for secret in (build.AUTHOR, build.COMMENT, "37.7749", build.CAMERA_MODEL):
        assert secret.encode() not in raw, secret
    assert tags.get("title") == build.MEDIA_TITLE  # descriptive fields retained by default
    assert [(s["codec_type"], s["codec_name"]) for s in po["streams"]] == \
        [(s["codec_type"], s["codec_name"]) for s in pn["streams"]]
    assert abs(float(po["format"]["duration"]) - float(pn["format"]["duration"])) < 0.1
    assert rotation(po) == rotation(pn)
    assert decodes(out)
    assert sha(src) == before
    for c in ("stream_layout", "packets_identical", "duration", "rotation", "decodes", "original_unchanged"):
        assert r["checks"][c]["ok"], c


def test_mp4_rotation_metadata_survives(tmp_path, capsys):
    src = build.mp4_private(tmp_path / "rot.mp4", rotation=90)
    assert rotation(probe(src)) == [90.0]
    out = tmp_path / "c.mp4"
    run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    assert rotation(probe(out)) == [90.0]


def test_mov_audio_language_retained(tmp_path, capsys):
    src = build.mov_private(tmp_path / "a.mov")
    out = tmp_path / "c.mov"
    run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    langs = [(s.get("tags") or {}).get("language") for s in probe(out)["streams"] if s["codec_type"] == "audio"]
    assert langs == ["eng"]


def test_remove_timestamps_option(tmp_path, capsys):
    src = build.mp4_private(tmp_path / "t.mp4")
    out = tmp_path / "c.mp4"
    run_main(["clean", str(src), "-o", str(out), "--also", "timestamp", "--json"], capsys)
    assert "creation_time" not in all_tags(probe(out))


def test_mkv_extras_skipped_by_default_and_explained(tmp_path, capsys):
    src = build.mkv_extras(tmp_path / "x.mkv")
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "skipped"
    for word in ("subtitle", "attachment", "chapter", "--allow-stream-loss"):
        assert word in r["reason"], word
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())

    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--allow-stream-loss",
                          "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    pn = probe(Path(r["output"]))
    assert sorted(s["codec_type"] for s in pn["streams"]) == ["audio", "video"]
    assert not pn.get("chapters")
    assert any("dropped" in w for w in r["warnings"])


def test_mp4_c2pa_skipped(tmp_path, capsys):
    src = build.mp4_c2pa(tmp_path / "cr.mp4")
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "skipped" and "provenance:c2pa" in r["flags"]


@pytest.mark.parametrize("codec,name", [("libmp3lame", "song.mp3"), ("flac", "song.flac"), ("aac", "song.m4a")])
def test_audio_tags_removed_artwork_kept(tmp_path, capsys, codec, name):
    src = build.audio_private(tmp_path / name, codec)
    out = tmp_path / ("clean-" + name)
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    assert {"format:artist", "format:comment"} <= keys(r["removed"])
    pn = probe(out)
    tags = all_tags(pn)
    assert "artist" not in tags and "comment" not in tags and "encoded_by" not in tags
    raw = out.read_bytes()
    assert build.AUTHOR.encode() not in raw and build.COMMENT.encode() not in raw and build.EDITOR.encode() not in raw
    assert tags.get("title") == "Fixture Song"
    pics = [s for s in pn["streams"] if s.get("disposition", {}).get("attached_pic")]
    assert len(pics) == 1
    assert any("artwork" in w and "kept" in w for w in r["warnings"])
    assert r["checks"]["packets_identical"]["ok"] and decodes(out)


@pytest.mark.parametrize("codec,name", [("libmp3lame", "s.mp3"), ("flac", "s.flac"), ("aac", "s.m4a")])
def test_audio_remove_artwork(tmp_path, capsys, codec, name):
    src = build.audio_private(tmp_path / name, codec)
    out = tmp_path / ("c-" + name)
    code, doc = run_main(["clean", str(src), "-o", str(out), "--remove-artwork", "--json"], capsys)
    assert result(doc)["status"] == "clean"
    assert [s["codec_type"] for s in probe(out)["streams"]] == ["audio"]


def test_artwork_with_exif_is_flagged_partial(tmp_path, capsys):
    src = build.audio_private(tmp_path / "e.flac", "flac", artwork_exif=True)
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "partial" and any("artwork carries its own image metadata" in u for u in r["unhandled"])
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o2"), "--remove-artwork", "--json"],
                         capsys)
    assert result(doc)["status"] == "clean"


def test_malformed_video_fails(tmp_path, capsys):
    bad = build.malformed(tmp_path)["mp4"]
    code, doc = run_main(["clean", str(bad), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "failed"


def test_missing_ffmpeg_reported(tmp_path, capsys, monkeypatch):
    src = build.mp4_private(tmp_path / "m.mp4")
    monkeypatch.setenv("VERISCRUB_FFPROBE", str(tmp_path / "missing-ffprobe"))
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "unsupported" and "ffprobe" in r["reason"]


def test_verify_command_against_original(tmp_path, capsys):
    src = build.mp4_private(tmp_path / "v.mp4")
    out = tmp_path / "c.mp4"
    run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    code, doc = run_main(["verify", str(out), "--original", str(src), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean" and code == 0, r
    assert {"format:artist", "format:location"} <= keys(r["removed"])
    code, doc = run_main(["verify", str(src), "--json"], capsys)
    assert result(doc)["status"] == "partial" and code == 3
