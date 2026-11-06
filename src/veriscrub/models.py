"""Data model shared by all handlers and the CLI."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Status(str, enum.Enum):
    """Outcome of one file. The values are part of the JSON contract."""

    INSPECTED = "inspected"      # inspect: fields were read successfully
    DRY_RUN = "dry_run"          # clean --dry-run: plan computed, nothing written
    CLEAN = "clean"              # every targeted field removed and verified; output published
    PARTIAL = "partial"          # output published, but some privacy-relevant data remains
    SKIPPED = "skipped"          # deliberately not modified (provenance, signature, unpreservable data, ...)
    UNSUPPORTED = "unsupported"  # format or variant outside the verified support matrix
    FAILED = "failed"            # error or failed verification; nothing published

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


_CONTROL = {i: f"\\x{i:02x}" for i in list(range(0, 32)) + [127] if i not in (9,)}


def safe_text(value: str, limit: int = 120) -> str:
    """Make an untrusted string safe to print: escape control/ANSI chars, truncate."""
    text = value.translate(_CONTROL).replace("\t", "\\t")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


@dataclass
class Field:
    """One metadata field found in a file.

    ``key`` is stable for the same field across the original and the cleaned
    copy. ``value`` is untrusted data and is redacted in reports by default.
    """

    key: str
    category: str
    value: str = ""
    removable: bool = True
    note: str = ""

    def to_dict(self, show_values: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "category": self.category}
        if show_values:
            d["value"] = safe_text(self.value)
        else:
            d["value"] = f"<redacted {len(self.value)} chars>" if self.value else ""
        if not self.removable:
            d["removable"] = False
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Inspection:
    """Everything a handler learned about one file (original or output)."""

    format: str
    fields: list[Field] = field(default_factory=list)
    # Informational flags, e.g. "provenance:c2pa", "pdf:annotations-with-authors:2".
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Reasons the file must be skipped by default, keyed by kind:
    # "provenance", "streams" (overridable) or "signed", "encrypted" (never modified).
    blockers: dict[str, list[str]] = field(default_factory=dict)
    # Privacy-relevant data the handler knowingly leaves in place (=> partial).
    unhandled: list[str] = field(default_factory=list)
    # Variant outside the verified matrix (=> unsupported for clean).
    unsupported_reason: str | None = None
    read_only: bool = False
    # Handler-private data (hashes, probe output); never serialised.
    info: dict[str, Any] = field(default_factory=dict)

    def block(self, kind: str, reason: str) -> None:
        self.blockers.setdefault(kind, []).append(reason)


@dataclass
class Result:
    path: str
    command: str
    status: Status
    format: str | None = None
    output: str | None = None
    report: str | None = None
    reason: str = ""
    fields: list[Field] = field(default_factory=list)
    removed: list[Field] = field(default_factory=list)
    planned: list[Field] = field(default_factory=list)
    retained: list[Field] = field(default_factory=list)
    remaining: list[Field] = field(default_factory=list)
    added: list[Field] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unhandled: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, show_values: bool = False) -> dict[str, Any]:
        def fl(items: list[Field]) -> list[dict[str, Any]]:
            return [f.to_dict(show_values) for f in items]

        d: dict[str, Any] = {
            "path": self.path,
            "command": self.command,
            "status": self.status.value,
            "format": self.format,
        }
        if self.output:
            d["output"] = self.output
        if self.report:
            d["report"] = self.report
        if self.reason:
            d["reason"] = self.reason
        if self.command == "inspect" or (self.command == "verify" and self.fields):
            d["fields"] = fl(self.fields)
        for name in ("planned", "removed", "retained", "remaining", "added"):
            items = getattr(self, name)
            if items or name in ("removed", "remaining") and self.command in ("clean", "verify"):
                d[name] = fl(items)
        for name in ("flags", "warnings", "unhandled"):
            if getattr(self, name):
                d[name] = list(getattr(self, name))
        if self.checks:
            d["checks"] = self.checks
        return d
