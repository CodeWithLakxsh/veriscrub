"""PDF document properties via pikepdf (qpdf): selective removal + full rewrite.

ExifTool and many editors update PDF metadata *incrementally*: the new values
are appended and the old revision (with the old author, etc.) stays in the
file. This handler always writes a complete new file with qpdf, which only
emits objects reachable from the current trailer, then re-opens the output and
scans its bytes for the removed values.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from veriscrub import containers
from veriscrub.handlers import Handler, Inspection, Options, check
from veriscrub.models import Field
from veriscrub.policy import Policy, looks_like_ai

_INFO_CATS = {
    "/Author": "author", "/Creator": "software", "/Producer": "software", "/Title": "descriptive",
    "/Subject": "descriptive", "/Keywords": "descriptive", "/CreationDate": "timestamp",
    "/ModDate": "timestamp", "/Trapped": "technical",
}

_XMP_CATS = [
    ("dc:creator", "author"), ("dc:contributor", "author"), ("dc:publisher", "author"),
    ("dc:rights", "rights"), ("xmpRights:", "rights"), ("dc:title", "descriptive"),
    ("dc:subject", "descriptive"), ("dc:description", "comment"), ("pdf:Keywords", "descriptive"),
    ("pdf:Producer", "software"), ("xmp:CreatorTool", "software"), ("xmp:CreateDate", "timestamp"),
    ("xmp:ModifyDate", "timestamp"), ("xmp:MetadataDate", "timestamp"), ("xmpMM:", "identifier"),
    ("pdfaid:", "technical"), ("pdfuaid:", "technical"), ("pdfx:", "technical"), ("dc:format", "technical"),
    ("photoshop:AuthorsPosition", "author"), ("photoshop:", "other"), ("pdf:Trapped", "technical"),
    ("dc:language", "technical"), ("dc:identifier", "identifier"), ("dc:source", "identifier"),
]


_NS = {
    "http://purl.org/dc/elements/1.1/": "dc", "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM", "http://ns.adobe.com/pdf/1.3/": "pdf",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights", "http://www.aiim.org/pdfa/ns/id/": "pdfaid",
    "http://www.aiim.org/pdfua/ns/id/": "pdfuaid", "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/pdfx/1.3/": "pdfx", "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/tiff/1.0/": "tiff", "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
}


def _qname(key: str) -> str:
    """pikepdf returns Clark notation '{ns}local'; convert known namespaces to 'prefix:local'."""
    if key.startswith("{") and "}" in key:
        ns, local = key[1:].split("}", 1)
        prefix = _NS.get(ns)
        if prefix:
            return f"{prefix}:{local}"
    return key


def _xmp_category(qname: str, value: str) -> str:
    if qname == "Iptc4xmpExt:DigitalSourceType":
        return "disclosure"
    for prefix, cat in _XMP_CATS:
        if qname.startswith(prefix):
            return "ai" if cat in ("comment", "descriptive", "other") and looks_like_ai(value) else cat
    return "ai" if looks_like_ai(value) else "other"


def _str(obj) -> str:
    if isinstance(obj, (list, tuple, set)):
        return "; ".join(_str(o) for o in obj)
    try:
        return str(obj)
    except Exception:  # noqa: BLE001 - binary strings
        return repr(obj)


class PdfHandler(Handler):
    name = "pdf"
    kinds = ("pdf",)
    python_modules = ("pikepdf",)

    def _open(self, path: Path):
        import pikepdf

        try:
            return pikepdf.open(path)
        except pikepdf.PasswordError:
            return None
        except pikepdf.PdfError as exc:
            raise containers.MalformedError(f"PDF could not be parsed: {exc}") from exc

    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:
        insp = Inspection(format="pdf")
        pdf = self._open(path)
        if pdf is None:
            insp.flags.append("encrypted")
            insp.block("encrypted", "PDF is encrypted with a user password; it was not opened or changed")
            return insp
        with pdf:
            if pdf.is_encrypted:
                insp.flags.append("encrypted")
                insp.block("encrypted", "PDF is encrypted; rewriting would change or drop its protection")
            info = pdf.trailer.get("/Info")
            if info is not None:
                for k, v in info.items():
                    val = _str(v)
                    cat = _INFO_CATS.get(k, "custom")
                    if cat == "descriptive" and looks_like_ai(val):
                        cat = "ai"
                    insp.fields.append(Field(key=f"info:{k[1:]}", category=cat, value=val,
                                             removable=cat != "technical"))
            if "/Metadata" in pdf.Root:
                try:
                    # Read-only: entering open_metadata() as a context manager would re-serialise XMP.
                    meta = pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False)
                    for k in list(meta.keys()):
                        q = _qname(k)
                        val = _str(meta.get(k, ""))
                        cat = _xmp_category(q, val)
                        insp.fields.append(Field(key=f"xmp:{q}", category=cat, value=val,
                                                 removable=cat not in ("technical", "disclosure")))
                except Exception as exc:  # noqa: BLE001 - malformed XMP
                    insp.warnings.append(f"document XMP could not be parsed ({type(exc).__name__}); "
                                         "it will be reported but not edited")
                    insp.unhandled.append("unparseable document-level XMP metadata stream")
            self._scan_structure(pdf, insp)
            insp.info["pages"] = len(pdf.pages)
            insp.info["page_hashes"] = [self._page_hash(p) for p in pdf.pages]
        return insp

    @staticmethod
    def _page_hash(page) -> str:
        import pikepdf

        h = hashlib.sha256()
        contents = page.obj.get("/Contents")
        items = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
        for c in items:
            h.update(c.read_bytes())
        return h.hexdigest()

    def _scan_structure(self, pdf, insp: Inspection) -> None:
        import pikepdf

        root = pdf.Root
        acro = root.get("/AcroForm")
        signed = False
        if acro is not None:
            if int(acro.get("/SigFlags", 0) or 0) & 1:
                signed = True
            for fld in acro.get("/Fields", []) or []:
                try:
                    if fld.get("/FT") == "/Sig" and fld.get("/V") is not None:
                        signed = True
                except Exception:  # noqa: BLE001
                    continue
        if "/Perms" in root:
            signed = True
        if signed:
            insp.flags.append("signed")
            insp.block("signed", "PDF carries a digital signature; any rewrite would invalidate it")

        annots_with_author = 0
        attachments = 0
        object_xmp = 0
        piece = 1 if "/PieceInfo" in root else 0
        for page in pdf.pages:
            if "/PieceInfo" in page.obj:
                piece += 1
            for a in page.obj.get("/Annots", []) or []:
                try:
                    if "/T" in a and a.get("/Subtype") not in ("/Link", "/Widget"):
                        annots_with_author += 1
                    if a.get("/Subtype") == "/FileAttachment":
                        attachments += 1
                except Exception:  # noqa: BLE001
                    continue
        names = root.get("/Names")
        if names is not None and "/EmbeddedFiles" in names:
            attachments += 1
        js = names is not None and "/JavaScript" in names
        root_meta = root.get("/Metadata")
        c2pa = False
        for obj in pdf.objects:
            try:
                if not isinstance(obj, pikepdf.Stream):
                    continue
                if obj.get("/Type") == "/Metadata" and (root_meta is None or obj.objgen != root_meta.objgen):
                    object_xmp += 1
                if str(obj.get("/Subtype", "")) in ("/application/c2pa", "/application#2Fc2pa"):
                    c2pa = True
            except Exception:  # noqa: BLE001
                continue
        if c2pa:
            insp.flags.append("provenance:c2pa")
            insp.block("provenance", "Content Credentials (C2PA) manifest embedded; rewriting would invalidate it")
        if annots_with_author:
            insp.unhandled.append(f"{annots_with_author} annotation(s)/comment(s) with an author name (not modified)")
        if attachments:
            insp.unhandled.append(f"{attachments} embedded file(s)/attachment(s) (not inspected)")
        if object_xmp:
            insp.unhandled.append(f"{object_xmp} object-level XMP stream(s), e.g. on images (not modified)")
        if piece:
            insp.unhandled.append(f"{piece} PieceInfo dictionary(ies) with application private data (not modified)")
        if js:
            insp.flags.append("javascript")
        if insp.unhandled:
            insp.flags.append("pdf:extra-metadata-locations")

    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:
        import pikepdf

        info_keys = [f.key.split(":", 1)[1] for f in targeted if f.key.startswith("info:")]
        xmp_keys = {f.key.split(":", 1)[1] for f in targeted if f.key.startswith("xmp:")}
        with pikepdf.open(src) as pdf:
            info = pdf.trailer.get("/Info")
            if info is not None:
                for k in info_keys:
                    if "/" + k in info:
                        del info["/" + k]
                if len(info.keys()) == 0:
                    del pdf.trailer["/Info"]
            if xmp_keys and "/Metadata" in pdf.Root:
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
                    for k in list(meta.keys()):
                        if _qname(k) in xmp_keys:
                            del meta[k]
            pdf.save(out, deterministic_id=policy.targets("identifier"), linearize=False,
                     fix_metadata_version=False)
        return ["full rewrite (no incremental update): unreachable old revisions were discarded"]

    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        same_pages = orig.info.get("pages") == out.info.get("pages")
        same_content = orig.info.get("page_hashes") == out.info.get("page_hashes")
        return {
            "page_count": check(same_pages, f"{out.info.get('pages')} page(s)"),
            "page_content_unchanged": check(same_content, "decoded page content streams are identical"),
        }
