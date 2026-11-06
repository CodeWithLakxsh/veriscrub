"""External tool discovery and a hardened subprocess runner.

* Commands are always argument arrays (never a shell string).
* Every call has a timeout.
* stdin is closed unless data is passed explicitly.
* File paths handed to tools are absolute, so a name such as ``-rf.jpg`` can
  never be parsed as an option.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_TIMEOUT = 120.0

ENV_OVERRIDES = {
    "exiftool": "VERISCRUB_EXIFTOOL",
    "ffmpeg": "VERISCRUB_FFMPEG",
    "ffprobe": "VERISCRUB_FFPROBE",
}

MIN_VERSIONS = {"exiftool": (12, 40), "ffmpeg": (5, 0), "ffprobe": (5, 0)}


class ToolError(RuntimeError):
    pass


class ToolMissing(ToolError):
    pass


@dataclass
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def err_text(self) -> str:
        return self.stderr.decode("utf-8", "replace").strip()


def find_tool(name: str) -> str | None:
    """Locate a tool. An env override wins; an override pointing nowhere means 'missing'."""
    env = ENV_OVERRIDES.get(name)
    if env and env in os.environ:
        candidate = os.environ[env]
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
        found = shutil.which(candidate) if candidate else None
        return found
    return shutil.which(name)


def require(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise ToolMissing(f"{name} not found (run `veriscrub doctor` for setup help)")
    return path


def run(argv: list[str], timeout: float = DEFAULT_TIMEOUT, input: bytes | None = None) -> Completed:
    if not argv or not isinstance(argv, list):
        raise ValueError("argv must be a non-empty list")
    env = dict(os.environ)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        cp = subprocess.run(
            argv,
            input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            shell=False,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{Path(argv[0]).name} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise ToolError(f"could not run {Path(argv[0]).name}: {exc}") from exc
    return Completed(cp.returncode, cp.stdout, cp.stderr)


def _parse_version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        m2 = re.search(r"N-(\d+)", text)  # git snapshot builds
        return (99, 0) if m2 else ()
    return tuple(int(g) for g in m.groups() if g is not None)


@lru_cache(maxsize=None)
def tool_version(name: str) -> tuple[str | None, str | None]:
    """Return (path, version string) or (None, None)."""
    path = find_tool(name)
    if not path:
        return None, None
    try:
        if name == "exiftool":
            cp = run([path, "-ver"], timeout=30)
            ver = cp.stdout.decode("utf-8", "replace").strip()
        else:
            cp = run([path, "-hide_banner", "-version"], timeout=30)
            first = cp.stdout.decode("utf-8", "replace").splitlines()[:1]
            ver = first[0].split(" ")[2] if first and len(first[0].split(" ")) > 2 else ""
    except ToolError:
        return path, None
    return path, ver or None


def version_ok(name: str, ver: str | None) -> bool:
    if not ver:
        return False
    parsed = _parse_version(ver)
    return bool(parsed) and parsed >= MIN_VERSIONS.get(name, (0,))


def ffmpeg_file_arg(path: Path) -> str:
    """FFmpeg URL for a local file: absolute path behind the file: protocol."""
    return "file:" + str(Path(path).resolve())


def clear_caches() -> None:
    tool_version.cache_clear()
