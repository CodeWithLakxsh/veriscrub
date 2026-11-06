"""JPEG, PNG and static WebP via ExifTool plus an independent structure parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriscrub import containers
from veriscrub.handlers import Handler, Inspection, Options, check
from veriscrub.handlers import exiftool as et
from veriscrub.models import Field
from veriscrub.policy import Policy, classify_exiftool, classify_png_text
from veriscrub.safeio import read_limited

MAX_IMAGE = 512 << 20

_ORIENTATION_KEYS = ("EXIF:IFD0:Orientation", "XMP:XMP-tiff:Orientation")


class ImageHandler(Handler):
    name = "image"
    kinds = ("jpeg", "png", "webp")
    tools = ("exiftool",)

    # ------------------------------------------------------------ inspect
    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:
        data = read_limited(path, MAX_IMAGE)
        struct = containers.parse_image(kind, data)  # raises MalformedError
        meta = et.read(path, opts.timeout)
        insp = Inspection(format=kind)
        insp.info["struct"] = struct
        insp.info["exif"] = meta
        err = meta.get("ExifTool:ExifTool:Error") or meta.get("ExifTool:Error")
        if err:
            raise containers.MalformedError(f"exiftool: {err}")
        warn = meta.get("ExifTool:ExifTool:Warning") or meta.get("ExifTool:Warning")
        if warn:
            insp.warnings.append(f"exiftool warning: {et.to_text(warn)[:200]}")

        for key, value in meta.items():
            g0, g1, tag = et.split_key(key)
            text = et.to_text(value)
            cat = classify_exiftool(g0, g1, tag, text)
            if cat is None:
                continue
            removable = cat not in ("technical", "disclosure", "provenance")
            insp.fields.append(Field(key=key, category=cat, value=text, removable=removable))
            if cat == "provenance":
                struct.c2pa = True

        if kind == "png":
            for ctype, keyword, value in struct.texts:
                if keyword == containers.XMP_KEYWORD or keyword.lower().startswith("raw profile type"):
                    continue  # XMP / legacy embedded EXIF: reported through ExifTool groups
                insp.fields.append(Field(key=f"PNG-text:{keyword}", category=classify_png_text(keyword, value),
                                         value=value, note=ctype))
            if struct.has_time_chunk:
                insp.fields.append(Field(key="PNG-chunk:tIME", category="timestamp", value="tIME"))

        if struct.c2pa:
            insp.flags.append("provenance:c2pa")
            insp.block("provenance", "Content Credentials (C2PA/JUMBF) detected; editing metadata would "
                                     "invalidate the signed manifest")
        if struct.animated:
            insp.unsupported_reason = ("animated image (APNG / animated WebP) is outside the verified matrix; "
                                       "only static images are cleaned")
        if struct.trailer_bytes:
            insp.unhandled.append(f"{struct.trailer_bytes} bytes after the JPEG end-of-image marker "
                                  "(e.g. MPF secondary images or an embedded motion-photo video) are kept "
                                  "and not inspected")
        if struct.mpf:
            insp.flags.append("jpeg:mpf")
        if struct.icc_hash:
            insp.flags.append("color-profile:icc")
        if struct.has_alpha:
            insp.flags.append("transparency")
        return insp

    # ------------------------------------------------------------ clean
    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:
        notes: list[str] = []
        current = src
        png_keys = {f.key.split(":", 1)[1] for f in targeted if f.key.startswith("PNG-text:")}
        drop_time = any(f.key == "PNG-chunk:tIME" for f in targeted)
        if kind == "png" and (png_keys or drop_time):
            data = read_limited(src, MAX_IMAGE)
            new, _removed = containers.png_remove_text(data, png_keys, drop_time)
            stage = work / "stage1.png"
            stage.write_bytes(new)
            current = stage

        args = self._delete_args(targeted, insp)
        if args:
            et.write(current, out, args, opts.timeout)
        else:
            out.write_bytes(current.read_bytes())
        return notes

    @staticmethod
    def _delete_args(targeted: list[Field], insp: Inspection) -> list[str]:
        args: list[str] = []
        seen: set[str] = set()

        def add(a: str) -> None:
            if a not in seen:
                seen.add(a)
                args.append(a)

        touches_xmp = False
        for f in targeted:
            if f.key.startswith("PNG-"):
                continue
            g0, g1, tag = et.split_key(f.key)
            if g0 == "MakerNotes":
                add("-MakerNotes:all=")
            elif g1 == "IFD1":
                add("-IFD1:all=")
            elif g1 == "GPS":
                add("-GPS:all=")
            elif g0 == "File" and tag == "Comment":
                add("-File:Comment=")
            else:
                add(f"-{g1}:{tag}=")
            if g0 == "XMP":
                touches_xmp = True
        if touches_xmp:
            # ExifTool stamps its own XMPToolkit when rewriting XMP; do not add one.
            had_toolkit = any(f.key == "XMP:XMP-x:XMPToolkit" for f in insp.fields)
            if not had_toolkit:
                add("-XMP-x:XMPToolkit=")
        return args

    # ------------------------------------------------------------ verify
    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        a: containers.ImageStructure = orig.info["struct"]
        b: containers.ImageStructure = out.info["struct"]
        res = {
            "image_data_unchanged": check(a.image_hash == b.image_hash,
                                          "compressed image data and non-metadata chunks are byte-identical"),
            "color_profile_preserved": check(a.icc_hash == b.icc_hash,
                                             "ICC profile present and identical" if a.icc_hash else "no ICC profile"),
            "transparency_preserved": check(a.has_alpha == b.has_alpha, "alpha channel" if b.has_alpha else "no alpha channel"),
        }
        oa = _orientation(orig.info["exif"])
        ob = _orientation(out.info["exif"])
        res["orientation_preserved"] = check(oa == ob, f"orientation={ob if ob is not None else 'none'}")
        return res

    def artifact_keys(self, out: Inspection) -> set[str]:
        return set()


def _orientation(meta: dict[str, Any]) -> Any:
    for k in _ORIENTATION_KEYS:
        if k in meta:
            return meta[k]
    return None
