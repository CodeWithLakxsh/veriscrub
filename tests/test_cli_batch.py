"""Batch processing, collisions, odd filenames, dry run, privacy of reports, failed verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from conftest import needs_exiftool, result, run_main, run_subprocess
from fixtures import build
from veriscrub.handlers.docx import DocxHandler
from veriscrub.safeio import WORKDIR_PREFIX


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*")) if d.exists() else []


def test_batch_mirrors_structure_and_handles_collisions(tmp_path, capsys):
    a = tmp_path / "in" / "a"
    b = tmp_path / "in" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    build.docx_private(a / "report.docx", comments=False, tracked=False)
    build.docx_private(b / "report.docx", comments=False, tracked=False)
    out = tmp_path / "out"
    code, doc = run_main(["clean", str(tmp_path / "in"), "--output-dir", str(out), "--json"], capsys)
    assert code == 0, doc
    assert {"a/report.docx", "b/report.docx"} <= set(tree(out))
    # Two explicit files with the same name into one directory -> second one is renamed, nothing overwritten.
    flat = tmp_path / "flat"
    code, doc = run_main(["clean", str(a / "report.docx"), str(b / "report.docx"), "--output-dir", str(flat),
                          "--json"], capsys)
    outs = sorted(Path(r["output"]).name for r in doc["results"])
    assert outs == ["report-1.docx", "report.docx"]
    assert any("name was taken" in w for w in doc["results"][1]["warnings"])


def test_existing_output_never_overwritten(tmp_path, capsys):
    src = build.docx_private(tmp_path / "r.docx", comments=False, tracked=False)
    out = tmp_path / "o"
    out.mkdir()
    (out / "r.docx").write_bytes(b"precious")
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--json"], capsys)
    assert Path(result(doc)["output"]).name == "r-1.docx"
    assert (out / "r.docx").read_bytes() == b"precious"
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--on-collision", "skip", "--json"], capsys)
    assert result(doc)["status"] == "skipped"
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--on-collision", "fail", "--json"], capsys)
    assert result(doc)["status"] == "failed"
    assert (out / "r.docx").read_bytes() == b"precious"


def test_default_output_next_to_original(tmp_path, capsys):
    src = build.markdown(tmp_path / "n.md")
    code, doc = run_main(["clean", str(src), "--front-matter-key", "author", "--json"], capsys)
    assert Path(result(doc)["output"]) == tmp_path / "n.cleaned.md"


@pytest.mark.parametrize("name", ["-leading hyphen.md", "spaced name.md", "ünïcödé 名前.md", "--help.md"])
def test_awkward_filenames(tmp_path, name):
    src = build.markdown(tmp_path / name)
    code, out = run_subprocess(["clean", "--json", "--front-matter-key", "email", "--output-dir",
                                str(tmp_path / "o"), "--", str(src)])
    doc = json.loads(out[out.index("{"):])
    assert result(doc)["status"] == "clean", out
    assert (tmp_path / "o" / name).exists()


@needs_exiftool
@pytest.mark.parametrize("name", ["-rf photo.jpg", "фото 写真.jpg"])
def test_awkward_filenames_through_exiftool(tmp_path, name):
    src = build.jpeg_private(tmp_path / "plain.jpg")
    target = tmp_path / name
    src.rename(target)
    code, out = run_subprocess(["clean", "--json", "--output-dir", str(tmp_path / "o"), "--", str(target)])
    doc = json.loads(out[out.index("{"):])
    assert result(doc)["status"] == "clean", out
    assert (tmp_path / "o" / name).exists()


def test_dry_run_writes_nothing(tmp_path, capsys):
    src = build.docx_private(tmp_path / "r.docx")
    out = tmp_path / "o"
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--dry-run", "--json"], capsys)
    r = result(doc)
    assert r["status"] == "dry_run" and r["planned"] and code == 0
    assert not out.exists() or not list(out.iterdir())


def test_reports_do_not_leak_values(tmp_path, capsys):
    src = build.docx_private(tmp_path / "r.docx")
    for cmd in (["inspect", str(src)], ["clean", str(src), "--output-dir", str(tmp_path / "o")]):
        run_main(cmd + ["--json"], capsys)
        code, text = run_main(cmd + ["--output-dir", str(tmp_path / "o2")] if cmd[0] == "clean" else cmd, capsys)
        for secret in (build.AUTHOR, build.EDITOR, build.COMPANY, "ACME-2024-0042"):
            assert secret not in text


def test_failed_verification_publishes_nothing(tmp_path, capsys, monkeypatch):
    src = build.docx_private(tmp_path / "r.docx", comments=False, tracked=False)
    before = sha(src)
    real_clean = DocxHandler.clean

    def sabotage(self, src_, out, *a, **k):
        notes = real_clean(self, src_, out, *a, **k)
        import zipfile

        # Corrupt the document body inside the output: integrity check must catch it.
        tmp = out.with_suffix(".tmp")
        with zipfile.ZipFile(out) as zi, zipfile.ZipFile(tmp, "w") as zo:
            for info in zi.infolist():
                data = zi.read(info.filename)
                if info.filename == "word/document.xml":
                    data = data.replace(b"quarterly", b"QUARTERLY")
                zo.writestr(info, data)
        os.replace(tmp, out)
        return notes

    monkeypatch.setattr(DocxHandler, "clean", sabotage)
    out = tmp_path / "o"
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "failed" and "document_content_unchanged" in r["reason"] and code == 1
    assert "output" not in r
    assert tree(out) == []  # no output and no leftover temp directory
    assert sha(src) == before


def test_strict_refuses_partial(tmp_path, capsys):
    src = build.docx_private(tmp_path / "r.docx")  # has comments -> partial
    out = tmp_path / "o"
    code, doc = run_main(["clean", str(src), "--output-dir", str(out), "--strict", "--json"], capsys)
    assert result(doc)["status"] == "failed" and "--strict" in result(doc)["reason"]
    assert tree(out) == []


def test_output_dir_inside_input_dir_is_excluded(tmp_path, capsys):
    d = tmp_path / "docs"
    d.mkdir()
    build.docx_private(d / "r.docx", comments=False, tracked=False)
    out = d / "cleaned"
    run_main(["clean", str(d), "--output-dir", str(out), "--json"], capsys)
    code, doc = run_main(["clean", str(d), "--output-dir", str(out), "--json"], capsys)
    assert [Path(r["path"]).name for r in doc["results"]] == ["r.docx"]
    assert not any(p.name.startswith(WORKDIR_PREFIX) for p in out.rglob("*"))


def test_human_output_and_exit_codes(tmp_path):
    src = build.text_file(tmp_path / "t.txt")
    code, out = run_subprocess(["clean", str(src)])
    assert code == 3 and "[skipped]" in out
    code, out = run_subprocess(["inspect", str(src)])
    assert code == 0 and "[inspected]" in out
