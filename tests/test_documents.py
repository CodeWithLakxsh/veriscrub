"""DOCX, PDF, TXT and Markdown handlers."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from conftest import keys, needs_pikepdf, result, run_main
from fixtures import build


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ DOCX

def test_docx_properties_removed_content_preserved(tmp_path, capsys):
    src = build.docx_private(tmp_path / "report.docx")
    before = sha(src)
    out = tmp_path / "out" / "report.docx"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "partial"  # comments + tracked changes are reported, not removed
    assert {"core:dc:creator", "core:cp:lastModifiedBy", "app:ep:Company", "app:ep:Manager",
            "custom:ClientMatter", "custom:Reviewer", "core:dc:description"} <= keys(r["removed"])
    assert any("comment" in u for u in r["unhandled"]) and any("tracked change" in u for u in r["unhandled"])
    assert sha(src) == before and r["checks"]["original_unchanged"]["ok"]
    # Re-open the output independently.
    with zipfile.ZipFile(out) as zo, zipfile.ZipFile(src) as zi:
        props = zo.read("docProps/core.xml") + zo.read("docProps/app.xml") + zo.read("docProps/custom.xml")
        for secret in (build.AUTHOR, build.EDITOR, build.COMPANY, "ACME-2024-0042", build.COMMENT):
            assert secret.encode() not in props
        assert b"Quarterly Report" in zo.read("docProps/core.xml")  # descriptive title retained by default
        for name in zi.namelist():
            if not name.startswith("docProps/"):
                assert zo.read(name) == zi.read(name), name
        assert build.DOC_BODY.encode() in zo.read("word/document.xml")
        assert zo.namelist() == zi.namelist()


def test_docx_without_review_data_is_clean(tmp_path, capsys):
    src = build.docx_private(tmp_path / "plain.docx", comments=False, tracked=False)
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "clean" and code == 0


def test_docx_signed_and_encrypted_are_skipped(tmp_path, capsys):
    signed = build.docx_private(tmp_path / "signed.docx", signed=True)
    enc = build.docx_encrypted(tmp_path / "locked.docx")
    code, doc = run_main(["clean", str(signed), str(enc), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    st = {Path(r["path"]).name: r for r in doc["results"]}
    assert st["signed.docx"]["status"] == "skipped" and "signed" in st["signed.docx"]["reason"]
    assert st["locked.docx"]["status"] == "skipped" and "encrypted" in st["locked.docx"]["reason"]
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())


def test_docx_malformed_fails_without_output(tmp_path, capsys):
    bad = build.malformed(tmp_path)["docx"]
    code, doc = run_main(["clean", str(bad), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "failed" and code == 1
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").iterdir())


# ------------------------------------------------------------------ PDF

@needs_pikepdf
def test_pdf_full_rewrite_purges_incremental_history(tmp_path, capsys):
    import pikepdf

    src = build.pdf_incremental(tmp_path / "report.pdf")
    raw = src.read_bytes()
    assert build.EDITOR.encode() in raw and build.AUTHOR.encode() in raw  # old + new revision
    out = tmp_path / "clean.pdf"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    assert {"info:Author", "info:Company", "xmp:dc:creator", "xmp:xmpMM:DocumentID"} <= keys(r["removed"])
    data = out.read_bytes()
    for secret in (build.AUTHOR, build.EDITOR, build.COMPANY, "11111111-2222-3333"):
        assert secret.encode() not in data, secret
    assert data.count(b"%%EOF") == 1
    with pikepdf.open(out) as pdf:
        assert "/Author" not in pdf.docinfo and "/Company" not in pdf.docinfo
        assert str(pdf.docinfo["/Title"]) == "Quarterly Report"
        assert build.DOC_BODY.encode() in pdf.pages[0].Contents.read_bytes()
        meta = pdf.open_metadata()
        assert "dc:creator" not in meta
    assert r["checks"]["page_content_unchanged"]["ok"]


@needs_pikepdf
def test_pdf_remove_ai_flag_removes_producer(tmp_path, capsys):
    src = build.pdf_incremental(tmp_path / "r.pdf")
    out = tmp_path / "c.pdf"
    run_main(["clean", str(src), "-o", str(out), "--remove-ai", "--json"], capsys)
    assert b"FixturePDF" not in out.read_bytes() and b"FixtureWriter" not in out.read_bytes()


@needs_pikepdf
def test_pdf_signed_and_encrypted_skipped_unchanged(tmp_path, capsys):
    signed = build.pdf_signed(tmp_path / "signed.pdf")
    enc = build.pdf_encrypted(tmp_path / "enc.pdf")
    hashes = {p: sha(p) for p in (signed, enc)}
    code, doc = run_main(["clean", str(signed), str(enc), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    st = {Path(r["path"]).name: r for r in doc["results"]}
    assert st["signed.pdf"]["status"] == "skipped" and "signature" in st["signed.pdf"]["reason"]
    assert st["enc.pdf"]["status"] == "skipped" and "encrypted" in st["enc.pdf"]["reason"]
    assert all(sha(p) == h for p, h in hashes.items())
    assert code == 3


@needs_pikepdf
def test_pdf_with_content_credentials_skipped(tmp_path, capsys):
    import pikepdf

    src = build.pdf_incremental(tmp_path / "r.pdf")
    cr = tmp_path / "cr.pdf"
    with pikepdf.open(src) as pdf:
        manifest = pikepdf.Stream(pdf, b"jumb....c2pa fixture")
        manifest["/Type"] = pikepdf.Name("/EmbeddedFile")
        manifest["/Subtype"] = pikepdf.Name("/application#2Fc2pa")
        pdf.Root["/AF"] = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Filespec"), F=pikepdf.String("manifest.c2pa"),
            EF=pikepdf.Dictionary(F=pdf.make_indirect(manifest))))])
        pdf.save(cr)
    code, doc = run_main(["clean", str(cr), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "skipped" and "provenance:c2pa" in r["flags"]


@needs_pikepdf
def test_pdf_malformed_fails(tmp_path, capsys):
    bad = build.malformed(tmp_path)["pdf"]
    code, doc = run_main(["clean", str(bad), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "failed"


def test_pdf_without_pikepdf_is_unsupported(tmp_path, capsys, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "pikepdf":
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    src = build.pdf_incremental(tmp_path / "r.pdf")
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "unsupported" and "pikepdf" in r["reason"]


# ------------------------------------------------------------------ text / Markdown

def test_plain_text_is_skipped_with_explanation(tmp_path, capsys):
    p = build.text_file(tmp_path / "letter.txt")
    code, doc = run_main(["clean", str(p), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    r = result(doc)
    assert r["status"] == "skipped" and "no embedded metadata" in r["reason"]
    code, doc = run_main(["inspect", str(p), "--json"], capsys)
    assert any("no standard embedded" in f for f in result(doc)["flags"])


@pytest.mark.parametrize("crlf", [False, True])
def test_markdown_front_matter_keys_removed_body_preserved(tmp_path, capsys, crlf):
    src = build.markdown(tmp_path / "notes.md", crlf=crlf)
    original = src.read_bytes()
    out = tmp_path / "clean.md"
    code, doc = run_main(["clean", str(src), "-o", str(out), "--front-matter-key", "author",
                          "--front-matter-key", "email", "--front-matter-key", "tags", "--json"], capsys)
    r = result(doc)
    assert r["status"] == "clean", r
    assert keys(r["removed"]) == {"frontmatter:author", "frontmatter:email", "frontmatter:tags"}
    new = out.read_bytes()
    nl = b"\r\n" if crlf else b"\n"
    body = original.split(b"---" + nl, 2)[2]
    assert new.endswith(body)                       # body byte-identical
    assert b"author: this line is body text" in new  # body line with the same key name survives
    assert build.AUTHOR.encode() not in new and b"jane@example.invalid" not in new and b"- launch" not in new
    assert b"title: Launch notes" in new and b"draft: true" in new
    assert src.read_bytes() == original


def test_markdown_without_keys_is_skipped_and_lists_keys(tmp_path, capsys):
    src = build.markdown(tmp_path / "n.md")
    code, doc = run_main(["inspect", str(src), "--json"], capsys)
    assert {"frontmatter:title", "frontmatter:author", "frontmatter:email", "frontmatter:tags",
            "frontmatter:draft"} == keys(result(doc)["fields"])
    code, doc = run_main(["clean", str(src), "--output-dir", str(tmp_path / "o"), "--json"], capsys)
    assert result(doc)["status"] == "skipped" and "--front-matter-key" in result(doc)["reason"]
