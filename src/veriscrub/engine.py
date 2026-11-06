"""Orchestration: inspect / clean / verify one file, and batch traversal."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from veriscrub import containers, tools
from veriscrub.detect import detect
from veriscrub.handlers import Handler, Options, check, get_handler
from veriscrub.handlers import exiftool as et
from veriscrub.models import Field, Inspection, Result, Status
from veriscrub.policy import Policy, classify_exiftool
from veriscrub.safeio import (WORKDIR_PREFIX, is_regular_file_nofollow, publish, scan_for_values,
                                  sha256_file, value_needles, workdir)

OVERRIDABLE = {"provenance": "allow_provenance_loss", "streams": "allow_stream_loss"}


# ---------------------------------------------------------------- traversal

@dataclass
class Item:
    path: Path
    rel: Path | None = None        # relative path inside a directory argument
    root: Path | None = None       # the directory argument it came from
    skip_reason: str = ""


def collect(inputs: list[Path], exclude: Path | None = None, recursive: bool = True) -> list[Item]:
    """Expand files and directories without following symlinks."""
    items: list[Item] = []
    excl = os.path.normcase(str(exclude.resolve())) if exclude else None
    for p in inputs:
        if os.path.islink(p):
            items.append(Item(p, skip_reason="symbolic link (not followed); pass the target path instead"))
            continue
        if p.is_dir():
            items.extend(_walk(p, excl, recursive))
        elif p.exists():
            if not is_regular_file_nofollow(p):
                items.append(Item(p, skip_reason="not a regular file"))
            else:
                items.append(Item(p))
        else:
            items.append(Item(p, skip_reason="file not found"))
    return items


def _walk(root: Path, excl: str | None, recursive: bool) -> Iterator[Item]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        keep = []
        for name in sorted(dirnames):
            full = d / name
            if name.startswith(WORKDIR_PREFIX):
                continue
            if excl and os.path.normcase(str(full.resolve())) == excl:
                continue
            if os.path.islink(full):
                yield Item(full, rel=full.relative_to(root), root=root,
                           skip_reason="symbolic link to a directory (not followed)")
                continue
            keep.append(name)
        dirnames[:] = keep if recursive else []
        for name in sorted(filenames):
            full = d / name
            item = Item(full, rel=full.relative_to(root), root=root)
            if os.path.islink(full):
                item.skip_reason = "symbolic link (not followed)"
            elif not is_regular_file_nofollow(full):
                item.skip_reason = "not a regular file"
            yield item


def default_output(item: Item, output: Path | None, output_dir: Path | None, single: bool) -> Path:
    if output is not None and single:
        return output
    if output_dir is not None:
        if item.rel is not None:
            return output_dir / item.rel  # mirror the directory structure
        return output_dir / item.path.name
    return item.path.with_name(f"{item.path.stem}.cleaned{item.path.suffix}")


# ---------------------------------------------------------------- helpers

class UnreadableError(OSError):
    pass


def _resolve(path: Path) -> tuple[str | None, str, Handler | None, list[str]]:
    det = detect(path)
    if det.kind is None and det.label.startswith("unreadable"):
        raise UnreadableError(det.label)
    warnings = []
    if det.ext_mismatch:
        warnings.append(f"extension '{path.suffix}' does not match the actual content ({det.label}); "
                        "the content type was used")
    handler = get_handler(det.kind) if det.kind else None
    return det.kind, det.label, handler, warnings


def _readonly_inspect(path: Path, label: str, opts: Options) -> Inspection | None:
    """Read-only inspection of formats outside the matrix, via ExifTool if installed."""
    if not tools.find_tool("exiftool"):
        return None
    try:
        meta = et.read(path, opts.timeout)
    except tools.ToolError:
        return None
    insp = Inspection(format=str(meta.get("File:File:FileType", label)), read_only=True)
    for key, value in meta.items():
        g0, g1, tag = et.split_key(key)
        text = et.to_text(value)
        cat = classify_exiftool(g0, g1, tag, text)
        if cat is not None:
            insp.fields.append(Field(key=key, category=cat, value=text, removable=False))
    return insp


def _base_result(path: Path, command: str, status: Status, fmt: str | None, reason: str = "") -> Result:
    return Result(path=str(path), command=command, status=status, format=fmt, reason=reason)


# ---------------------------------------------------------------- inspect

def inspect_file(path: Path, policy: Policy, opts: Options) -> Result:
    try:
        kind, label, handler, warnings = _resolve(path)
    except UnreadableError as exc:
        return _base_result(path, "inspect", Status.FAILED, None,
                            f"cannot read the file ({str(exc).removeprefix('unreadable ').strip('()')}); "
                            "check permissions. On macOS, files inside the Photos library are protected: "
                            "export the photo first (Photos > File > Export > Export Unmodified Original)")
    if handler is None and label.startswith("encrypted"):
        r = _base_result(path, "inspect", Status.INSPECTED, None, f"{label}: contents cannot be inspected")
        r.flags = ["encrypted"]
        return r
    if handler is None:
        ro = _readonly_inspect(path, label, opts)
        if ro is None or not ro.fields and kind is None and label == "unknown":
            return _base_result(path, "inspect", Status.UNSUPPORTED, None,
                                f"{label}: not in the supported-format matrix and no verified read-only backend")
        r = _base_result(path, "inspect", Status.INSPECTED, ro.format,
                         f"{label}: read-only inspection via ExifTool; cleaning is not supported for this format")
        r.fields = ro.fields
        r.flags = ["read-only"]
        r.warnings = warnings
        return r
    missing = handler.missing()
    if missing:
        return _base_result(path, "inspect", Status.UNSUPPORTED, kind, missing)
    try:
        insp = handler.inspect(path, kind, opts)
    except (containers.MalformedError, ValueError) as exc:
        return _base_result(path, "inspect", Status.FAILED, kind, f"malformed or unreadable file: {exc}")
    except tools.ToolError as exc:
        return _base_result(path, "inspect", Status.FAILED, kind, str(exc))
    r = _base_result(path, "inspect", Status.INSPECTED, kind)
    r.fields = insp.fields
    r.flags = insp.flags
    r.warnings = warnings + insp.warnings + [b for kind_, bl in insp.blockers.items() if kind_ != "noop" for b in bl]
    r.unhandled = insp.unhandled
    r.planned = handler.select_targets(insp, policy, opts)
    if insp.unsupported_reason:
        r.reason = insp.unsupported_reason
    return r


# ---------------------------------------------------------------- clean

def _blocked(insp: Inspection, opts: Options) -> str | None:
    for kind, reasons in insp.blockers.items():
        if kind in OVERRIDABLE and getattr(opts, OVERRIDABLE[kind]):
            continue
        hint = ""
        if kind in OVERRIDABLE:
            hint = f" (override: --{OVERRIDABLE[kind].replace('_', '-')})"
        return "; ".join(reasons) + hint
    return None


def _residue(handler: Handler, out_path: Path, out_insp: Inspection, removed: list[Field]) -> list[str]:
    # A removed value that is legitimately part of a field still present in the output
    # (e.g. the GPS date inside the retained DateTimeOriginal) is not residue.
    kept_text = [f.value for f in out_insp.fields if f.value]
    values: list[str] = []
    for f in removed:
        for v in [f.value, *(part for part in f.value.split("; ") if part)]:
            if not any(v.strip() in t for t in kept_text):
                values.append(v)
    needles = value_needles(values)
    if not needles:
        return []
    hits: list[str] = []
    for label, target in handler.residue_scope(out_path, out_insp):
        if isinstance(target, Path):
            found = scan_for_values(target, list(needles))
        else:
            found = {n for n in needles if n in target}
        for n in found:
            key = next((f.key for f in removed if needles[n] in f.value), "?")
            msg = f"value of removed field {key} still occurs in {label} bytes"
            if msg not in hits:
                hits.append(msg)
    return hits


def clean_file(item: Item, dest: Path, policy: Policy, opts: Options, *, dry_run: bool = False,
               strict: bool = False, on_collision: str = "rename", reserved: set[str] | None = None) -> Result:
    path = item.path
    if item.skip_reason:
        return _base_result(path, "clean", Status.SKIPPED, None, item.skip_reason)
    try:
        kind, label, handler, warnings = _resolve(path)
    except UnreadableError as exc:
        return _base_result(path, "clean", Status.FAILED, None,
                            f"cannot read the file ({str(exc).removeprefix('unreadable ').strip('()')}); "
                            "check permissions. On macOS, files inside the Photos library are protected: "
                            "export the photo first (Photos > File > Export > Export Unmodified Original)")
    if handler is None and label.startswith("encrypted"):
        return _base_result(path, "clean", Status.SKIPPED, None,
                            f"{label}: reported only; encrypted documents are never opened or changed")
    if handler is None:
        return _base_result(path, "clean", Status.UNSUPPORTED, None,
                            f"{label}: not in the supported-format matrix (use `inspect` for read-only details)")
    missing = handler.missing()
    if missing:
        return _base_result(path, "clean", Status.UNSUPPORTED, kind, missing)
    try:
        original_hash = sha256_file(path)
        insp = handler.inspect(path, kind, opts)
    except (containers.MalformedError, ValueError) as exc:
        return _base_result(path, "clean", Status.FAILED, kind, f"malformed or unreadable file: {exc}")
    except (tools.ToolError, OSError) as exc:
        return _base_result(path, "clean", Status.FAILED, kind, str(exc))

    r = _base_result(path, "clean", Status.FAILED, kind)
    r.flags = list(insp.flags)
    r.warnings = warnings + list(insp.warnings)
    if insp.unsupported_reason:
        r.status, r.reason = Status.UNSUPPORTED, insp.unsupported_reason
        return r
    blocked = _blocked(insp, opts)
    if blocked:
        r.status, r.reason = Status.SKIPPED, blocked
        r.unhandled = list(insp.unhandled)
        return r

    targeted = handler.select_targets(insp, policy, opts)
    target_keys = {f.key for f in targeted}
    if dry_run:
        r.status = Status.DRY_RUN
        r.planned = targeted
        r.retained = [f for f in insp.fields if f.key not in target_keys]
        r.unhandled = list(insp.unhandled)
        r.output = str(dest)
        r.reason = "dry run: nothing was written"
        return r

    try:
        with workdir(dest.parent) as wd:
            tmp = wd / f"output{dest.suffix or '.' + kind}"
            notes: list[str] = []
            if targeted:
                notes = handler.clean(path, tmp, kind, insp, targeted, policy, opts, wd)
            else:
                shutil.copyfile(path, tmp)
                notes = ["no targeted fields found; the output is a byte-for-byte copy"]
            r.warnings.extend(notes)

            out_insp = handler.inspect(tmp, kind, opts)
            artifacts = handler.artifact_keys(out_insp)
            orig_keys = {f.key for f in insp.fields}
            out_keys = {f.key for f in out_insp.fields}
            r.removed = [f for f in targeted if f.key not in out_keys]
            r.remaining = [f for f in out_insp.fields
                           if f.key not in artifacts and (
                               f.key in target_keys or (f.removable and policy.targets(f.category)))]
            r.retained = [f for f in out_insp.fields if f.key in orig_keys and f not in r.remaining]
            r.added = [f for f in out_insp.fields if f.key not in orig_keys]
            r.unhandled = list(out_insp.unhandled)

            checks = handler.integrity(insp, out_insp, opts)
            residue = _residue(handler, tmp, out_insp, r.removed)
            checks["removed_values_absent"] = check(not residue, "; ".join(residue) or
                                                    "no removed value found in the output bytes")
            checks["original_unchanged"] = check(sha256_file(path) == original_hash, "SHA-256 of the original")
            r.checks = checks

            integrity_failed = [k for k, v in checks.items() if not v["ok"] and k != "removed_values_absent"]
            if integrity_failed:
                r.status = Status.FAILED
                r.reason = "verification failed: " + ", ".join(integrity_failed) + "; nothing was published"
                return r
            partial = bool(r.remaining or r.unhandled or residue)
            if partial and strict:
                r.status = Status.FAILED
                r.reason = "--strict: output would be only partially clean; nothing was published"
                return r
            final = publish(tmp, dest, on_collision=on_collision, reserved=reserved)
            if final is None:
                r.status, r.reason = Status.SKIPPED, f"output exists: {dest} (--on-collision skip)"
                return r
            r.output = str(final)
            if final != dest:
                r.warnings.append(f"output name was taken; wrote {final.name} instead")
            r.status = Status.PARTIAL if partial else Status.CLEAN
            if partial:
                bits = []
                if r.remaining:
                    bits.append(f"{len(r.remaining)} targeted field(s) could not be removed")
                if r.unhandled:
                    bits.append("privacy-relevant data outside this handler's scope remains")
                if residue:
                    bits.append("removed values still occur in the file bytes")
                r.reason = "; ".join(bits)
            return r
    except FileExistsError as exc:
        r.status, r.reason = Status.FAILED, str(exc)
    except (containers.MalformedError, ValueError) as exc:
        r.status, r.reason = Status.FAILED, f"output could not be validated: {exc}; nothing was published"
    except (tools.ToolError, OSError) as exc:
        r.status, r.reason = Status.FAILED, f"{exc}; nothing was published"
    return r


# ---------------------------------------------------------------- verify

def verify_file(path: Path, policy: Policy, opts: Options, original: Path | None = None) -> Result:
    try:
        kind, label, handler, warnings = _resolve(path)
    except UnreadableError as exc:
        return _base_result(path, "verify", Status.FAILED, None,
                            f"cannot read the file ({str(exc).removeprefix('unreadable ').strip('()')}); "
                            "check permissions. On macOS, files inside the Photos library are protected: "
                            "export the photo first (Photos > File > Export > Export Unmodified Original)")
    if handler is None:
        return _base_result(path, "verify", Status.UNSUPPORTED, None, f"{label}: not in the supported-format matrix")
    missing = handler.missing()
    if missing:
        return _base_result(path, "verify", Status.UNSUPPORTED, kind, missing)
    try:
        insp = handler.inspect(path, kind, opts)
        orig_insp = handler.inspect(original, kind, opts) if original else None
    except (containers.MalformedError, ValueError) as exc:
        return _base_result(path, "verify", Status.FAILED, kind, f"malformed or unreadable file: {exc}")
    except tools.ToolError as exc:
        return _base_result(path, "verify", Status.FAILED, kind, str(exc))
    r = _base_result(path, "verify", Status.CLEAN, kind)
    r.flags, r.warnings, r.unhandled = insp.flags, warnings + insp.warnings, insp.unhandled
    artifacts = handler.artifact_keys(insp)
    r.remaining = [f for f in insp.fields if f.key not in artifacts and f.removable and policy.targets(f.category)]
    r.retained = [f for f in insp.fields if f not in r.remaining]
    if orig_insp is not None:
        keys = {f.key for f in insp.fields}
        orig_keys = {f.key for f in orig_insp.fields}
        targeted = handler.select_targets(orig_insp, policy, opts)
        r.removed = [f for f in targeted if f.key not in keys]
        r.remaining += [f for f in insp.fields if f.key in {t.key for t in targeted} and f not in r.remaining]
        r.added = [f for f in insp.fields if f.key not in orig_keys]
        if "roles" in orig_insp.info and "kept" not in orig_insp.info:
            orig_insp.info["kept"] = _guess_kept(orig_insp, insp)
        checks = handler.integrity(orig_insp, insp, opts)
        residue = _residue(handler, path, insp, r.removed)
        checks["removed_values_absent"] = check(not residue, "; ".join(residue) or
                                                "no removed value found in the file bytes")
        r.checks = checks
        if any(not v["ok"] for k, v in checks.items() if k != "removed_values_absent"):
            r.status = Status.FAILED
            r.reason = "integrity checks against the original failed"
            return r
        if residue:
            r.status = Status.PARTIAL
    if r.remaining or r.unhandled:
        r.status = Status.PARTIAL
        r.reason = (f"{len(r.remaining)} field(s) in the selected categories are present"
                    + ("; data outside this handler's scope remains" if r.unhandled else ""))
    elif r.status == Status.CLEAN and orig_insp is not None:
        r.reason = (f"{len(r.removed)} field(s) removed relative to the original; "
                    f"{len(r.checks)} integrity check(s) passed; no fields in the selected categories remain")
    elif r.status == Status.CLEAN:
        r.reason = "no fields in the selected categories were found by this handler"
    return r


def _guess_kept(orig: Inspection, out: Inspection) -> list[int]:
    """Verifying a cleaned media file against its original: infer which streams were kept."""
    streams = orig.info["probe"]["streams"]
    n_out = len(out.info["probe"]["streams"])
    if len(streams) == n_out:
        return [s["index"] for s in streams]
    av = [s["index"] for s, role in zip(streams, orig.info["roles"], strict=True) if role in ("video", "audio")]
    art = [s["index"] for s, role in zip(streams, orig.info["roles"], strict=True) if role == "artwork"]
    return sorted(av + art) if len(av) + len(art) == n_out else av


def summarize(results: list[Result]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r.status.value] = out.get(r.status.value, 0) + 1
    return out
