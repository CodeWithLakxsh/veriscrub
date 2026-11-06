"""`veriscrub doctor`: report which backends are installed and what they enable."""

from __future__ import annotations

import platform
import sys
from typing import Any

from veriscrub import tools

FORMAT_BACKENDS = {
    "JPEG / PNG / static WebP": ["exiftool"],
    "MP4 / MOV / MKV": ["ffmpeg", "ffprobe"],
    "MP3 / M4A / FLAC": ["ffmpeg", "ffprobe"],
    "PDF": ["pikepdf"],
    "DOCX": [],
    "TXT / Markdown": [],
    "Other formats (read-only inspect)": ["exiftool"],
}

REQUIRED_MUXERS = ("streamhash", "mp4", "mov", "matroska", "ipod", "mp3", "flac", "null", "image2pipe")

INSTALL_HINTS = {
    "Darwin": {"exiftool": "brew install exiftool", "ffmpeg": "brew install ffmpeg",
               "pikepdf": 'pipx inject veriscrub pikepdf  (or: pip install "veriscrub[pdf]")'},
    "Linux": {"exiftool": "sudo apt-get install libimage-exiftool-perl   (Fedora: sudo dnf install perl-Image-ExifTool)",
              "ffmpeg": "sudo apt-get install ffmpeg   (Fedora: sudo dnf install ffmpeg-free)",
              "pikepdf": 'pip install "veriscrub[pdf]"'},
    "Windows": {"exiftool": "winget install -e --id OliverBetz.ExifTool   (or: choco install exiftool)",
                "ffmpeg": "winget install -e --id Gyan.FFmpeg   (or: choco install ffmpeg)",
                "pikepdf": 'pip install "veriscrub[pdf]"'},
}


def _muxers(ffmpeg: str) -> set[str]:
    try:
        cp = tools.run([ffmpeg, "-hide_banner", "-muxers"], timeout=30)
    except tools.ToolError:
        return set()
    names: set[str] = set()
    for line in cp.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and set(parts[0]) <= set("DE."):
            names.update(parts[1].split(","))
    return names


def run_doctor() -> dict[str, Any]:
    system = platform.system()
    hints = INSTALL_HINTS.get(system, INSTALL_HINTS["Linux"])
    checks: list[dict[str, Any]] = []
    py_ok = sys.version_info >= (3, 10)
    checks.append({"name": "python", "ok": py_ok, "version": platform.python_version(),
                   "path": sys.executable, "detail": "Python 3.10+ required"})
    status: dict[str, bool] = {}
    for name in ("exiftool", "ffmpeg", "ffprobe"):
        path, ver = tools.tool_version(name)
        ok = bool(path) and tools.version_ok(name, ver)
        detail = ""
        if not path:
            detail = f"not found on PATH (override with {tools.ENV_OVERRIDES[name]}); install: " + \
                     hints["ffmpeg" if name == "ffprobe" else name]
        elif not ok:
            want = ".".join(map(str, tools.MIN_VERSIONS[name]))
            detail = f"version {ver or 'unknown'} is older than the tested minimum {want}"
        if name == "ffmpeg" and path:
            missing = [m for m in REQUIRED_MUXERS if m not in _muxers(path)]
            if missing:
                ok = False
                detail = "missing muxers: " + ", ".join(missing)
            else:
                detail = detail or "stream copy + streamhash verification available"
        if name == "exiftool" and ok:
            detail = "EXIF/IPTC/XMP read and delete available"
        checks.append({"name": name, "ok": ok, "path": path, "version": ver, "detail": detail})
        status[name] = ok
    try:
        import pikepdf  # noqa: F401

        status["pikepdf"] = True
        checks.append({"name": "pikepdf", "ok": True, "version": pikepdf.__version__,
                       "path": getattr(pikepdf, "__file__", None), "detail": "qpdf-based full rewrite available"})
    except ImportError:
        status["pikepdf"] = False
        checks.append({"name": "pikepdf", "ok": False, "version": None, "path": None,
                       "detail": "optional; needed for PDF. install: " + hints["pikepdf"]})
    formats = {fmt: all(status.get(dep, False) for dep in deps) for fmt, deps in FORMAT_BACKENDS.items()}
    return {
        "platform": f"{system} {platform.release()}",
        "checks": checks,
        "formats": formats,
        "ok": all(c["ok"] for c in checks),
        "network": "veriscrub never makes network requests; all processing is local",
    }
