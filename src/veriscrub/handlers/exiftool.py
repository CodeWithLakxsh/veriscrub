"""Thin ExifTool wrapper (read JSON, delete tags) with Unicode-safe argument passing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from veriscrub import tools

BASE_READ = ["-j", "-a", "-G0:1", "-s", "-n", "-api", "LargeFileSupport=1"]


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            i = 1
            while f"{k}#{i}" in out:
                i += 1
            k = f"{k}#{i}"
        out[k] = v
    return out


def _exec(args: list[str], files: list[Path], timeout: float) -> tools.Completed:
    exe = tools.require("exiftool")
    paths = [str(Path(f).resolve()) for f in files]
    if os.name == "nt":
        # Perl's argv is ANSI on Windows; pass everything through a UTF-8 argfile on stdin.
        lines = ["-charset", "filename=utf8", *args, *paths]
        if any("\n" in a or "\r" in a for a in lines):
            raise tools.ToolError("argument contains a newline")
        return tools.run([exe, "-@", "-"], timeout=timeout, input=("\n".join(lines) + "\n").encode("utf-8"))
    for p in paths:
        if "\n" in p or "\r" in p:
            raise tools.ToolError("file names containing newlines are not supported by the ExifTool backend")
    return tools.run([exe, *args, *paths], timeout=timeout)


def read(path: Path, timeout: float, numeric: bool = True) -> dict[str, Any]:
    args = BASE_READ if numeric else [a for a in BASE_READ if a != "-n"]
    cp = _exec(args, [path], timeout)
    text = cp.stdout.decode("utf-8", "replace").strip()
    if not text:
        raise tools.ToolError(f"exiftool returned no data: {cp.err_text[:300]}")
    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise tools.ToolError(f"exiftool returned invalid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise tools.ToolError("exiftool returned an unexpected structure")
    return data[0]


def extract(path: Path, tag: str, timeout: float) -> bytes:
    """Binary value of one tag (e.g. an MPF secondary image), or b"" if absent."""
    cp = _exec(["-b", f"-{tag}"], [path], timeout)
    return cp.stdout if cp.returncode == 0 else b""


def write(src: Path, out: Path, delete_args: list[str], timeout: float) -> None:
    if out.exists():
        raise FileExistsError(out)
    exe_args = [*delete_args, "-o", str(Path(out).resolve())]
    # The output path goes after -o; ExifTool treats it as an option value.
    cp = _exec(exe_args, [src], timeout)
    if cp.returncode != 0 or not out.exists():
        raise tools.ToolError(f"exiftool failed to write: {cp.err_text[:400] or cp.stdout[:400]!r}")


def split_key(key: str) -> tuple[str, str, str]:
    base = key.split("#", 1)[0]
    parts = base.split(":")
    if len(parts) >= 3:
        return parts[0], parts[1], ":".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[0], parts[1]
    return "", "", base


def to_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "; ".join(to_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
