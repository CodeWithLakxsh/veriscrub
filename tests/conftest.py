from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from veriscrub import tools  # noqa: E402
from veriscrub.cli import main  # noqa: E402

HAVE_EXIFTOOL = shutil.which("exiftool") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    import pikepdf  # noqa: F401

    HAVE_PIKEPDF = True
except ImportError:  # pragma: no cover
    HAVE_PIKEPDF = False
try:
    import PIL  # noqa: F401

    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

needs_exiftool = pytest.mark.skipif(not (HAVE_EXIFTOOL and HAVE_PIL), reason="needs ExifTool and Pillow")
needs_ffmpeg = pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_PIL), reason="needs FFmpeg/ffprobe and Pillow")
needs_pikepdf = pytest.mark.skipif(not HAVE_PIKEPDF, reason="needs pikepdf")


@pytest.fixture(autouse=True)
def _fresh_tool_cache():
    tools.clear_caches()
    yield
    tools.clear_caches()


def run_main(args: list[str], capsys) -> tuple[int, dict | str]:
    """Run the CLI in-process (so tests can monkeypatch). Returns (exit code, parsed JSON or text)."""
    code = main(args)
    out = capsys.readouterr().out
    if "--json" in args:
        return code, json.loads(out)
    return code, out


def run_subprocess(args: list[str], env: dict | None = None, cwd: Path | None = None) -> tuple[int, str]:
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    cp = subprocess.run([sys.executable, "-m", "veriscrub", *args], capture_output=True, text=True,
                        encoding="utf-8", env=e, cwd=str(cwd) if cwd else None, timeout=600)
    return cp.returncode, cp.stdout + cp.stderr


def result(doc: dict, index: int = 0) -> dict:
    return doc["results"][index]


def keys(items: list[dict]) -> set[str]:
    return {i["key"] for i in items}
