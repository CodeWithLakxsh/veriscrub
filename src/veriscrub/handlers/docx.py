"""DOCX document properties (docProps/core.xml, app.xml, custom.xml).

Scope is deliberately narrow and explicit:

* **Cleaned:** standard core properties, extended (app) properties and custom
  properties, by category.
* **Reported, never modified:** comments, tracked changes (insertions,
  deletions, moves) and their author names, the people list, embedded
  objects, images in word/media, the document thumbnail, revision-session IDs.
* **Skipped:** digitally signed packages (any change breaks the signature),
  encrypted packages (they are not ZIP files at all), macro-enabled documents.

Every other ZIP member is copied with identical bytes, and that is verified.
"""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

from veriscrub import containers
from veriscrub.handlers import Handler, Inspection, Options, check
from veriscrub.models import Field
from veriscrub.policy import Policy, looks_like_ai

MAX_MEMBERS = 20000
MAX_TOTAL = 2 << 30
MAX_PART = 256 << 20

PROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")

NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "op": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
_PREFIX = {v: k for k, v in NS.items()}

_CORE = {
    "dc:creator": "author", "cp:lastModifiedBy": "author", "cp:lastPrinted": "timestamp",
    "dcterms:created": "timestamp", "dcterms:modified": "timestamp", "cp:revision": "identifier",
    "dc:identifier": "identifier", "dc:title": "descriptive", "dc:subject": "descriptive",
    "cp:keywords": "descriptive", "cp:category": "descriptive", "cp:contentStatus": "descriptive",
    "cp:contentType": "descriptive", "cp:version": "descriptive", "dc:description": "comment",
    "dc:language": "technical",
}
_APP = {
    "ep:Company": "author", "ep:Manager": "author", "ep:Template": "software", "ep:Application": "software",
    "ep:AppVersion": "software", "ep:TotalTime": "identifier", "ep:HyperlinkBase": "descriptive",
    "ep:TitlesOfParts": "descriptive", "ep:HeadingPairs": "descriptive",
}
# Statistics and layout flags are technical and kept.

_TRACKED = re.compile(rb"<w:(ins|del|moveFrom|moveTo)\b")
_AUTHOR = re.compile(rb'w:author="([^"]*)"')
_COMMENT = re.compile(rb"<w:comment\b")
_RSID = re.compile(rb"w:rsid\w*=")


def _qn(tag: str) -> str:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return f"{_PREFIX.get(ns, ns)}:{local}"
    return tag


def _parse(xml: bytes) -> ET.Element:
    # OOXML never uses DTDs; refusing them blocks entity-expansion attacks.
    if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml:
        raise containers.MalformedError("XML part declares a DTD/entities, which OOXML never uses")
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise containers.MalformedError(f"XML part is not well-formed: {exc}") from exc


def _register(xml: bytes) -> None:
    for _ev, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=("start-ns",)):
        if prefix and not prefix.startswith("ns"):
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass


def _serialize(root: ET.Element, original: bytes) -> bytes:
    _register(original)
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body).encode("utf-8")


class DocxHandler(Handler):
    name = "docx"
    kinds = ("docx",)

    def _open(self, path: Path) -> zipfile.ZipFile:
        try:
            zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise containers.MalformedError(f"not a readable ZIP package: {exc}") from exc
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise containers.MalformedError("too many ZIP members")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise containers.MalformedError("duplicate ZIP member names (ambiguous package)")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL or any(i.file_size > MAX_PART for i in infos):
            raise containers.MalformedError("package expands beyond the size limit")
        return zf

    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:
        insp = Inspection(format="docx")
        with self._open(path) as zf:
            names = zf.namelist()
            try:
                hashes = {n: hashlib.sha256(zf.read(n)).hexdigest() for n in names}
            except (zipfile.BadZipFile, zlib.error, RuntimeError, OSError) as exc:
                raise containers.MalformedError(f"corrupt ZIP member: {exc}") from exc
            insp.info["hashes"] = hashes
            insp.info["order"] = names
            if "word/document.xml" not in names:
                raise containers.MalformedError("word/document.xml is missing")
            _parse(zf.read("word/document.xml"))  # must be well-formed

            props_bytes = b""
            for part, table in (("docProps/core.xml", _CORE), ("docProps/app.xml", _APP)):
                if part in names:
                    raw = zf.read(part)
                    props_bytes += raw
                    root = _parse(raw)
                    prefix = "core" if "core" in part else "app"
                    for el in root:
                        q = _qn(el.tag)
                        cat = table.get(q, "technical" if prefix == "app" else "other")
                        text = "".join(el.itertext()).strip()
                        if cat in ("comment", "descriptive") and looks_like_ai(text):
                            cat = "ai"
                        insp.fields.append(Field(key=f"{prefix}:{q}", category=cat, value=text,
                                                 removable=cat != "technical"))
            if "docProps/custom.xml" in names:
                raw = zf.read("docProps/custom.xml")
                props_bytes += raw
                root = _parse(raw)
                for el in root:
                    name = el.get("name", "")
                    text = "".join(el.itertext()).strip()
                    insp.fields.append(Field(key=f"custom:{name}", category="ai" if looks_like_ai(text) else "custom",
                                             value=text))
            insp.info["props_bytes"] = props_bytes

            if any(n.startswith("_xmlsignatures/") for n in names):
                insp.flags.append("signed")
                insp.block("signed", "document is digitally signed; changing any part would invalidate it")

            story = [n for n in names if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$", n)]
            tracked = 0
            authors: set[bytes] = set()
            for n in story:
                raw = zf.read(n)
                tracked += len(_TRACKED.findall(raw)) if "comments" not in n else 0
                authors.update(_AUTHOR.findall(raw))
            comments = len(_COMMENT.findall(zf.read("word/comments.xml"))) if "word/comments.xml" in names else 0
            if comments:
                insp.unhandled.append(f"{comments} comment(s) (with author names) are kept; this tool does not "
                                      "remove comments")
            if tracked:
                insp.unhandled.append(f"{tracked} tracked change(s) (with author names) are kept; accept or "
                                      "reject them in your editor")
            if authors:
                insp.flags.append(f"review-authors:{len(authors)}")
            if "word/people.xml" in names:
                insp.unhandled.append("word/people.xml lists reviewer identities (kept)")
            emb = [n for n in names if n.startswith("word/embeddings/")]
            media = [n for n in names if n.startswith("word/media/")]
            if emb:
                insp.warnings.append(f"{len(emb)} embedded object(s) in word/embeddings/ are kept and not inspected")
            if media:
                insp.warnings.append(f"{len(media)} image(s) in word/media/ are kept; their own EXIF/XMP is not "
                                     "inspected (clean images before inserting them)")
            if any(n.startswith("docProps/thumbnail") for n in names):
                insp.warnings.append("docProps thumbnail image is kept (it shows the first page)")
            if "word/settings.xml" in names and _RSID.search(zf.read("word/settings.xml")):
                insp.warnings.append("revision-session IDs (w:rsid) are kept; they can link documents edited "
                                     "in the same session")
            insp.info["counts"] = {"comments": comments, "tracked_changes": tracked, "embeddings": len(emb),
                                   "media": len(media)}
        return insp

    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:
        drop = {f.key for f in targeted}
        with self._open(src) as zin, zipfile.ZipFile(out, "x") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename in PROPS_PARTS:
                    data = self._rewrite(info.filename, data, drop)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zout.writestr(zi, data)
        return []

    def _rewrite(self, part: str, data: bytes, drop: set[str]) -> bytes:
        root = _parse(data)
        changed = False
        for el in list(root):
            if part.endswith("custom.xml"):
                key = f"custom:{el.get('name', '')}"
            else:
                key = f"{'core' if 'core' in part else 'app'}:{_qn(el.tag)}"
            if key in drop:
                root.remove(el)
                changed = True
        if not changed:
            return data
        if part.endswith("custom.xml"):
            # Re-number the remaining properties (pid must start at 2 and be unique).
            for i, el in enumerate(root, start=2):
                el.set("pid", str(i))
        return _serialize(root, data)

    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        a, b = orig.info["hashes"], out.info["hashes"]
        same_members = orig.info["order"] == out.info["order"]
        changed = [n for n in a if n not in PROPS_PARTS and a.get(n) != b.get(n)]
        return {
            "package_members": check(same_members, f"{len(b)} ZIP member(s), same names and order"),
            "document_content_unchanged": check(not changed,
                                                "all parts outside docProps/ are byte-identical"
                                                if not changed else f"changed: {', '.join(changed[:5])}"),
        }

    def residue_scope(self, path: Path, insp: Inspection) -> list[tuple[str, Path | bytes]]:
        return [("docProps", insp.info.get("props_bytes", b""))]

