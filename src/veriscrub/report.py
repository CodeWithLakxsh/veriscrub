"""Human-readable record of what `clean` removed and kept (--report / --zip).

The report deliberately contains the private values that were removed. That is its purpose: a
personal record. It starts with a warning so it is not shared together with the cleaned file.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from veriscrub import __version__
from veriscrub.models import Field, Result, safe_text
from veriscrub.policy import DESCRIPTIONS

REPORT_SUFFIX = ".removed-metadata.txt"
_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def _num(text: str) -> float | None:
    try:
        return float(str(text).split()[0])
    except (ValueError, IndexError):
        return None


def gps_summary(fields: list[Field]) -> list[str]:
    """Readable position lines from removed GPS fields (EXIF numeric values or ISO 6709 strings)."""
    by_tag: dict[str, str] = {}
    for f in fields:
        if f.category == "location":
            by_tag.setdefault(f.key.split(":")[-1].lower(), f.value)
    lat = lon = alt = None
    if "gpslatitude" in by_tag and "gpslongitude" in by_tag:
        lat, lon = _num(by_tag["gpslatitude"]), _num(by_tag["gpslongitude"])
        if lat is not None and by_tag.get("gpslatituderef", "N").upper().startswith("S"):
            lat = -abs(lat)
        if lon is not None and by_tag.get("gpslongituderef", "E").upper().startswith("W"):
            lon = -abs(lon)
        alt = _num(by_tag.get("gpsaltitude", ""))
        if alt is not None and str(by_tag.get("gpsaltituderef", "0")).strip() in ("1", "Below Sea Level"):
            alt = -alt
    else:
        for f in fields:
            m = _ISO6709.match(f.value.strip()) if f.category == "location" else None
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                alt = float(m.group(3)) if m.group(3) else None
                break
    if lat is None or lon is None:
        return []
    lines = [
        f"GPS position : {abs(lat):.6f} {'N' if lat >= 0 else 'S'}, {abs(lon):.6f} {'E' if lon >= 0 else 'W'}"
        f"   (decimal: {lat:.6f}, {lon:.6f})",
    ]
    if alt is not None:
        lines.append(f"Altitude     : {alt:.1f} m")
    lines.append(f"Map link     : https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}")
    lines.append("               (only a link; VeriScrub made no network request)")
    return lines


def _section(fields: list[Field], display: dict[str, str]) -> list[str]:
    lines: list[str] = []
    by_cat: dict[str, list[Field]] = {}
    for f in fields:
        by_cat.setdefault(f.category, []).append(f)
    for cat in sorted(by_cat):
        items = by_cat[cat]
        lines.append("")
        lines.append(f"  [{cat}] {DESCRIPTIONS.get(cat, '')} ({len(items)})")
        width = min(48, max(len(f.key) for f in items))
        for f in items:
            value = display.get(f.key, f.value)
            lines.append(f"    {safe_text(f.key, 80):<{width}}  {safe_text(value, 4000)}")
    return lines


def render(result: Result, original: Path, cleaned_name: str, original_sha: str, cleaned_sha: str,
           display_before: dict[str, str], display_after: dict[str, str]) -> str:
    now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    passed = sum(1 for c in result.checks.values() if c["ok"])
    out: list[str] = [
        "VeriScrub report: metadata removed from your file",
        "=" * 60,
        "",
        "!! PRIVATE: this report lists the private data that was REMOVED.",
        "!! Keep it for your records. Share only the cleaned file, never this report.",
        "",
        f"Original     : {safe_text(str(original), 400)}",
        f"Cleaned file : {safe_text(cleaned_name, 400)}",
        f"Status       : {result.status.value}   ({passed}/{len(result.checks)} verification checks passed)",
        f"Format       : {result.format}",
        f"SHA-256 orig : {original_sha}",
        f"SHA-256 clean: {cleaned_sha}",
        f"Created      : {now} by veriscrub {__version__}",
    ]
    if result.reason:
        out.append(f"Note         : {safe_text(result.reason, 600)}")
    gps = gps_summary(result.removed)
    if gps:
        out += ["", "WHERE THE PHOTO SAID IT WAS TAKEN (removed)", "-" * 60, *gps]
    out += ["", f"REMOVED ({len(result.removed)} fields)", "-" * 60]
    out += _section(result.removed, display_before) if result.removed else ["  (nothing was removed)"]
    if result.remaining:
        out += ["", f"STILL PRESENT although targeted ({len(result.remaining)})", "-" * 60]
        out += _section(result.remaining, display_after)
    out += ["", f"KEPT ({len(result.retained)} fields; needed for display or not selected for removal)", "-" * 60]
    out += _section(result.retained, display_after) if result.retained else ["  (none)"]
    if result.added:
        out += ["", f"ADDED by the writing tool ({len(result.added)})", "-" * 60]
        out += _section(result.added, display_after)
    if result.unhandled:
        out += ["", "NOT HANDLED (still in the file)", "-" * 60, *[f"  ! {safe_text(u, 600)}" for u in result.unhandled]]
    out += ["", "VERIFICATION", "-" * 60]
    for name, c in result.checks.items():
        out.append(f"  [{'ok' if c['ok'] else 'FAIL'}] {name}: {safe_text(c.get('detail', ''), 300)}")
    notes = [w for w in result.warnings if w]
    if notes:
        out += ["", "NOTES", "-" * 60, *[f"  - {safe_text(n, 600)}" for n in notes]]
    out += [
        "",
        "VeriScrub removes the metadata fields it can inspect. It does not detect or remove watermarks",
        "and does not claim that a file is free of every hidden signal.",
        "",
    ]
    return "\n".join(out)
