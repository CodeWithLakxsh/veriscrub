"""Plain text and Markdown.

Plain text has no standard embedded author/generator fields, so there is
nothing to clean. For Markdown, YAML front matter keys can be removed, but
only the keys the user names explicitly (``--front-matter-key``); the body is
preserved byte-for-byte and that is verified. Prose is never rewritten.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veriscrub import containers
from veriscrub.handlers import Handler, Inspection, Options, check
from veriscrub.models import Field
from veriscrub.policy import Policy
from veriscrub.safeio import read_limited

MAX_TEXT_FILE = 64 << 20
_OPEN = re.compile(r"^---[ \t]*(\r?\n)")
_CLOSE = re.compile(r"^(---|\.\.\.)[ \t]*$")
_KEY = re.compile(r"""^(?:"([^"]+)"|'([^']+)'|([^\s#\-][^:]*?))[ \t]*:(?:[ \t]|$)""")


@dataclass
class FrontMatter:
    prefix: str             # BOM + opening fence line
    blocks: list[tuple[str | None, list[str]]]  # (key or None, lines incl. newline)
    closing: str            # closing fence line incl. newline
    body: str


def split_front_matter(text: str) -> FrontMatter | None:
    bom = "﻿" if text.startswith("﻿") else ""
    rest = text[len(bom):]
    m = _OPEN.match(rest)
    if not m:
        return None
    lines = rest[m.end():].splitlines(keepends=True)
    blocks: list[tuple[str | None, list[str]]] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if _CLOSE.match(stripped):
            body = "".join(lines[i + 1:])
            return FrontMatter(bom + rest[:m.end()], blocks, line, body)
        km = _KEY.match(stripped)
        if km and not stripped.startswith((" ", "\t")):
            blocks.append((next(g for g in km.groups() if g is not None).strip(), [line]))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            blocks.append((None, [line]))
    return None  # no closing fence: not front matter


class TextHandler(Handler):
    name = "text"
    kinds = ("text", "markdown")

    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:
        raw = read_limited(path, MAX_TEXT_FILE)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise containers.MalformedError("not valid UTF-8 text") from exc
        insp = Inspection(format=kind)
        insp.info["raw"] = raw
        fm = split_front_matter(text) if kind == "markdown" else None
        insp.info["fm"] = fm
        insp.flags.append("plain-text: no standard embedded author/generator fields")
        if fm:
            for key, lines in fm.blocks:
                if key is None:
                    continue
                value = "".join(lines).split(":", 1)[1].strip() if ":" in lines[0] else ""
                insp.fields.append(Field(key=f"frontmatter:{key}", category="descriptive", value=value,
                                         removable=False, note="removed only when named with --front-matter-key"))
            insp.flags.append(f"front-matter:{len(insp.fields)} key(s)")
        requested = opts.front_matter_keys
        if not requested:
            what = ("Markdown front matter keys can be removed with --front-matter-key KEY"
                    if fm else "nothing to clean")
            insp.block("noop", f"plain text/Markdown has no embedded metadata fields; {what}. "
                               "The text itself was not reviewed.")
        elif kind == "text" or not fm:
            insp.block("noop", "no YAML front matter found; nothing to clean")
        else:
            present = {f.key.split(":", 1)[1] for f in insp.fields}
            missing = [k for k in requested if k not in present]
            if missing:
                insp.warnings.append(f"front matter key(s) not present: {', '.join(missing)}")
        return insp

    def select_targets(self, insp: Inspection, policy: Policy, opts: Options) -> list[Field]:
        wanted = {f"frontmatter:{k}" for k in opts.front_matter_keys}
        return [f for f in insp.fields if f.key in wanted]

    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:
        fm: FrontMatter = insp.info["fm"]
        drop = {f.key.split(":", 1)[1] for f in targeted}
        kept = "".join("".join(lines) for key, lines in fm.blocks if key not in drop)
        new = fm.prefix + kept + fm.closing + fm.body
        with open(out, "xb") as fh:
            fh.write(new.encode("utf-8"))
        return ["Markdown body copied unchanged; only the named front matter keys were removed"]

    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        a: FrontMatter | None = orig.info.get("fm")
        b: FrontMatter | None = out.info.get("fm")
        if a is None or b is None:
            same = hashlib.sha256(orig.info["raw"]).digest() == hashlib.sha256(out.info["raw"]).digest()
            return {"content_unchanged": check(same, "file bytes identical")}
        retained_a = {k: lines for k, lines in a.blocks if k and f"frontmatter:{k}" in {f.key for f in out.fields}}
        retained_b = {k: lines for k, lines in b.blocks if k}
        return {
            "body_unchanged": check(a.body == b.body, "Markdown body is byte-identical"),
            "other_keys_unchanged": check(all(retained_b.get(k) == v for k, v in retained_a.items()),
                                          "remaining front matter lines are identical"),
        }

    def residue_scope(self, path: Path, insp: Inspection) -> list[tuple[str, Path | bytes]]:
        fm: FrontMatter | None = insp.info.get("fm")
        if not fm:
            return []
        return [("front matter", "".join("".join(lines) for _k, lines in fm.blocks).encode("utf-8"))]
