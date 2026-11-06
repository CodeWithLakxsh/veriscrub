"""Command-line interface: doctor, inspect, clean, verify."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from veriscrub import __version__
from veriscrub.doctor import run_doctor
from veriscrub.engine import clean_file, collect, default_output, inspect_file, summarize, verify_file
from veriscrub.handlers import Options
from veriscrub.models import Field, Result, Status, safe_text
from veriscrub.policy import ALL_REMOVABLE, DEFAULT, DESCRIPTIONS, PROTECTED, Policy

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ATTENTION = 0, 1, 2, 3
_UTF8_OUT = True

def _category_lines() -> str:
    def mark(c: str) -> str:
        return "default " if c in DEFAULT else ("never   " if c in PROTECTED else "optional")
    return "\n".join(f"  {c:<12} {mark(c)}  {DESCRIPTIONS[c]}" for c in (*ALL_REMOVABLE, *PROTECTED))


EPILOG = f"""\
categories (default = removed by default; optional = --remove-ai adds ai,software,
--also CAT adds any, --all adds every one; never = protected):
{_category_lines()}

exit codes: 0 ok | 1 a file failed | 2 usage error | 3 some files partial/skipped/unsupported

This tool checks the metadata fields it can inspect. It does not detect or remove
watermarks, does not change detector scores, and never certifies that a file is
human-made or free of hidden signals.
"""


def _policy_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("policy")
    g.add_argument("--remove-ai", action="store_true",
                   help="also remove AI generation parameters (prompts, seeds, workflows) and software tags")
    g.add_argument("--also", action="append", metavar="CATEGORY[,CATEGORY]", default=[],
                   help="also remove these categories (e.g. timestamp,descriptive)")
    g.add_argument("--all", dest="everything", action="store_true",
                   help="remove every removable category (protected categories are always kept)")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")
    p.add_argument("--show-values", action="store_true",
                   help="include (escaped, truncated) field values in the report; off by default")
    p.add_argument("--timeout", type=float, default=120.0, metavar="SECONDS",
                   help="timeout per external tool call (default 120)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="veriscrub",
        description="Offline metadata inspection and verified cleanup for images, video, audio, PDF, DOCX "
                    "and Markdown. Originals are never modified.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"veriscrub {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check installed backends and what they enable")
    d.add_argument("--json", action="store_true")

    i = sub.add_parser("inspect", help="list metadata fields (values redacted by default)",
                       epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    i.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    i.add_argument("--no-recursive", action="store_true", help="do not descend into subdirectories")
    _common(i)
    _policy_args(i)

    c = sub.add_parser("clean", help="write cleaned copies after verifying them",
                       epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    out = c.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", type=Path, help="output file (single input file only)")
    out.add_argument("--output-dir", type=Path, help="write cleaned copies here (required for directories)")
    c.add_argument("--dry-run", action="store_true", help="show what would be removed; write nothing")
    c.add_argument("--strict", action="store_true", help="do not publish partially cleaned outputs")
    bundle = c.add_mutually_exclusive_group()
    bundle.add_argument("--report", action="store_true",
                        help="also write <name>.removed-metadata.txt listing every removed field WITH its value "
                             "(private: keep it, share only the cleaned file)")
    bundle.add_argument("--zip", action="store_true",
                        help="write one <name>.zip containing the cleaned file and the removed-metadata report")
    c.add_argument("--on-collision", choices=("rename", "skip", "fail"), default="rename",
                   help="when the output name exists: rename to name-1.ext (default), skip, or fail")
    c.add_argument("--no-recursive", action="store_true", help="do not descend into subdirectories")
    g = c.add_argument_group("format options")
    g.add_argument("--allow-stream-loss", action="store_true",
                   help="video/audio: drop subtitles, chapters, attachments and data streams instead of skipping")
    g.add_argument("--allow-provenance-loss", action="store_true",
                   help="process files with Content Credentials (C2PA) even though the manifest will be invalidated")
    g.add_argument("--remove-artwork", action="store_true", help="audio: drop embedded cover art")
    g.add_argument("--front-matter-key", action="append", default=[], metavar="KEY",
                   help="Markdown: remove this top-level front matter key (repeatable)")
    _common(c)
    _policy_args(c)

    v = sub.add_parser("verify", help="check a cleaned file (optionally against its original)",
                       epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    v.add_argument("path", type=Path, metavar="CLEANED")
    v.add_argument("--original", type=Path, help="original file to compare against (integrity + removed fields)")
    v.add_argument("--front-matter-key", action="append", default=[], metavar="KEY")
    v.add_argument("--remove-artwork", action="store_true", help=argparse.SUPPRESS)
    _common(v)
    _policy_args(v)
    return ap


# ---------------------------------------------------------------- output

def _emit_json(command: str, results: list[Result], show_values: bool, policy: Policy | None,
               extra: dict[str, Any] | None = None) -> None:
    doc: dict[str, Any] = {"tool": "veriscrub", "version": __version__, "command": command}
    if policy is not None:
        doc["policy"] = policy.to_dict()
    doc["results"] = [r.to_dict(show_values) for r in results]
    doc["summary"] = summarize(results)
    if extra:
        doc.update(extra)
    json.dump(doc, sys.stdout, indent=2, ensure_ascii=not _UTF8_OUT)
    sys.stdout.write("\n")


def _group(fields: list[Field]) -> str:
    by: dict[str, list[str]] = {}
    for f in fields:
        by.setdefault(f.category, []).append(safe_text(f.key, 60))
    parts = []
    for cat in sorted(by):
        keys = by[cat]
        shown = ", ".join(keys[:4]) + (f", +{len(keys) - 4} more" if len(keys) > 4 else "")
        parts.append(f"{cat} ×{len(keys)} ({shown})")
    return "; ".join(parts)


def _print_human(results: list[Result], show_values: bool) -> None:
    w = sys.stdout.write
    for r in results:
        line = f"[{r.status.value}] {safe_text(r.path, 200)}"
        if r.output:
            line += f" -> {safe_text(r.output, 200)}"
        if r.format:
            line += f"  ({r.format})"
        w(line + "\n")
        if r.reason:
            w(f"    {safe_text(r.reason, 600)}\n")
        if r.report:
            w(f"    report: {safe_text(r.report, 300)}\n")
        if r.command == "inspect" and r.fields:
            planned = {f.key for f in r.planned}
            for f in r.fields:
                mark = "*" if f.key in planned else " "
                val = safe_text(f.value, 80) if show_values else (f"<{len(f.value)} chars>" if f.value else "")
                w(f"   {mark} {f.category:<12} {safe_text(f.key, 70):<48} {val}\n")
            if planned:
                w("    (* = removed by `clean` under the current policy)\n")
        for label, items in (("would remove", r.planned if r.command != "inspect" else []),
                             ("removed", r.removed), ("remaining", r.remaining), ("added", r.added)):
            if items:
                w(f"    {label}: {_group(items)}\n")
        if r.retained and r.command in ("clean",) and r.status != Status.DRY_RUN:
            w(f"    retained: {len(r.retained)} field(s)\n")
        for u in r.unhandled:
            w(f"    ! {safe_text(u, 300)}\n")
        for fl in r.flags:
            w(f"    flag: {safe_text(fl, 200)}\n")
        for wa in r.warnings:
            w(f"    note: {safe_text(wa, 300)}\n")
        failed = [k for k, v in r.checks.items() if not v["ok"]]
        if r.checks:
            w(f"    checks: {len(r.checks) - len(failed)}/{len(r.checks)} passed"
              + (f" (failed: {', '.join(failed)})" if failed else "") + "\n")
    s = summarize(results)
    if len(results) > 1:
        w("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(s.items())) + "\n")


def _exit_code(results: list[Result]) -> int:
    statuses = {r.status for r in results}
    if Status.FAILED in statuses:
        return EXIT_FAILED
    if statuses & {Status.PARTIAL, Status.SKIPPED, Status.UNSUPPORTED}:
        return EXIT_ATTENTION
    return EXIT_OK


def _print_doctor(rep: dict[str, Any]) -> None:
    w = sys.stdout.write
    w(f"veriscrub {__version__} on {rep['platform']}\n")
    for c in rep["checks"]:
        mark = "ok " if c["ok"] else "-- "
        w(f"  {mark}{c['name']:<9} {c.get('version') or '':<12} {c.get('detail') or ''}\n")
    w("formats:\n")
    for fmt, ok in rep["formats"].items():
        w(f"  {'available  ' if ok else 'unavailable'} {fmt}\n")
    w(rep["network"] + "\n")


# ---------------------------------------------------------------- main

def _stdout_safe() -> bool:
    """Never crash on file names the console encoding cannot represent; True if stdout is UTF-8."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass
    return enc == "utf8"


def main(argv: list[str] | None = None) -> int:
    global _UTF8_OUT
    _UTF8_OUT = _stdout_safe()
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "doctor":
        rep = run_doctor()
        if args.json:
            json.dump({"tool": "veriscrub", "version": __version__, "command": "doctor", **rep},
                      sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            _print_doctor(rep)
        return EXIT_OK if rep["ok"] else EXIT_ATTENTION

    try:
        policy = Policy.build(remove_ai=args.remove_ai, also=args.also, everything=args.everything)
    except ValueError as exc:
        ap.error(str(exc))
    opts = Options(timeout=args.timeout,
                   allow_stream_loss=getattr(args, "allow_stream_loss", False),
                   allow_provenance_loss=getattr(args, "allow_provenance_loss", False),
                   remove_artwork=getattr(args, "remove_artwork", False),
                   front_matter_keys=getattr(args, "front_matter_key", []),
                   bundle="zip" if getattr(args, "zip", False) else ("report" if getattr(args, "report", False)
                                                                     else None))

    results: list[Result] = []
    if args.command == "inspect":
        for item in collect(args.paths, recursive=not args.no_recursive):
            if item.skip_reason:
                results.append(Result(str(item.path), "inspect", Status.SKIPPED, reason=item.skip_reason))
            else:
                results.append(inspect_file(item.path, policy, opts))
    elif args.command == "clean":
        has_dir = any(p.is_dir() and not p.is_symlink() for p in args.paths)
        if has_dir and not args.output_dir:
            ap.error("cleaning a directory requires --output-dir (originals are never modified in place)")
        items = collect(args.paths, exclude=args.output_dir, recursive=not args.no_recursive)
        single = len(items) == 1 and not has_dir
        if args.output and not single:
            ap.error("-o/--output works with exactly one input file; use --output-dir for several")
        reserved: set[str] = set()
        for item in items:
            dest = default_output(item, args.output, args.output_dir, single)
            results.append(clean_file(item, dest, policy, opts, dry_run=args.dry_run, strict=args.strict,
                                      on_collision=args.on_collision, reserved=reserved))
    elif args.command == "verify":
        results.append(verify_file(args.path, policy, opts, original=args.original))

    if args.json:
        _emit_json(args.command, results, args.show_values, policy)
    else:
        _print_human(results, args.show_values)
    return _exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
