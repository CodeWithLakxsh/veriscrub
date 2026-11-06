"""Format handlers and the registry that maps detected kinds to them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriscrub.models import Field, Inspection
from veriscrub.policy import Policy


class Options:
    """Per-run options shared by handlers."""

    def __init__(self, **kw: Any) -> None:
        self.timeout: float = kw.get("timeout", 120.0)
        self.allow_stream_loss: bool = kw.get("allow_stream_loss", False)
        self.allow_provenance_loss: bool = kw.get("allow_provenance_loss", False)
        self.remove_artwork: bool = kw.get("remove_artwork", False)
        self.front_matter_keys: list[str] = kw.get("front_matter_keys", []) or []
        # None, "report" (cleaned file + .removed-metadata.txt) or "zip" (both inside one zip)
        self.bundle: str | None = kw.get("bundle")


class Handler:
    name = "base"
    kinds: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    python_modules: tuple[str, ...] = ()

    def missing(self) -> str | None:
        """Return a human-readable reason if a dependency is missing."""
        from veriscrub import tools

        for t in self.tools:
            if not tools.find_tool(t):
                return f"{t} is required for this format but was not found (see `veriscrub doctor`)"
        for mod in self.python_modules:
            try:
                __import__(mod)
            except ImportError:
                return (f"Python package '{mod}' is required for this format; install with "
                        f"`pip install \"veriscrub[pdf]\"` (see `veriscrub doctor`)")
        return None

    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:  # pragma: no cover - abstract
        raise NotImplementedError

    def select_targets(self, insp: Inspection, policy: Policy, opts: Options) -> list[Field]:
        return [f for f in insp.fields if f.removable and policy.targets(f.category)]

    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:  # pragma: no cover - abstract
        """Write a cleaned copy of ``src`` to ``out``; return notes for the report."""
        raise NotImplementedError

    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        return {}

    def residue_scope(self, path: Path, insp: Inspection) -> list[tuple[str, Path | bytes]]:
        return [("file", path)]

    def display_values(self, path: Path, opts: Options) -> dict[str, str]:
        """Human-readable values for the report (e.g. '37 deg 46' 30" N'); default: raw values."""
        return {}

    def artifact_keys(self, out: Inspection) -> set[str]:
        """Keys a backend always writes (e.g. a bare muxer name); reported as 'added', not 'remaining'."""
        return set()


def check(ok: bool, detail: str = "") -> dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}


_REGISTRY: dict[str, Handler] = {}


def get_handler(kind: str) -> Handler | None:
    if not _REGISTRY:
        from veriscrub.handlers.av import AVHandler
        from veriscrub.handlers.docx import DocxHandler
        from veriscrub.handlers.image import ImageHandler
        from veriscrub.handlers.pdf import PdfHandler
        from veriscrub.handlers.text import TextHandler

        for h in (ImageHandler(), AVHandler(), PdfHandler(), DocxHandler(), TextHandler()):
            for k in h.kinds:
                _REGISTRY[k] = h
    return _REGISTRY.get(kind)
