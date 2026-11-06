"""MP4, MOV, MKV video and MP3, M4A, FLAC audio via ffprobe + FFmpeg stream copy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from veriscrub import containers, tools
from veriscrub.handlers import Handler, Inspection, Options, check
from veriscrub.models import Field
from veriscrub.policy import AV_STRUCTURAL, Policy, classify_av

VIDEO_KINDS = ("mp4", "mov", "mkv")
AUDIO_KINDS = ("mp3", "m4a", "flac")

_FORMAT_NAMES = {
    "mp4": "mov,mp4", "mov": "mov,mp4", "m4a": "mov,mp4", "mkv": "matroska", "mp3": "mp3", "flac": "flac",
}
_MUXER = {"mp4": "mp4", "mov": "mov", "m4a": "ipod", "mkv": "matroska", "mp3": "mp3", "flac": "flac"}

_DURATION_TOL = 0.1  # seconds, or 1 %, whichever is larger


def probe(path: Path, timeout: float) -> dict[str, Any]:
    exe = tools.require("ffprobe")
    cp = tools.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                    "-show_chapters", tools.ffmpeg_file_arg(path)], timeout=timeout)
    try:
        data = json.loads(cp.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise containers.MalformedError(f"ffprobe output unreadable: {exc}") from exc
    if cp.returncode != 0 or "format" not in data:
        raise containers.MalformedError(f"ffprobe could not parse the file: {cp.err_text[:300]}")
    return data


def _stream_role(s: dict[str, Any]) -> str:
    ctype = s.get("codec_type", "unknown")
    if ctype == "video" and s.get("disposition", {}).get("attached_pic"):
        return "artwork"
    return ctype


def _rotation(s: dict[str, Any]) -> float:
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return float(sd["rotation"]) % 360
            except (TypeError, ValueError):
                pass
    try:
        return float((s.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0.0


def _duration(p: dict[str, Any]) -> float | None:
    try:
        return float(p["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


class AVHandler(Handler):
    name = "av"
    kinds = VIDEO_KINDS + AUDIO_KINDS
    tools = ("ffmpeg", "ffprobe")

    # ------------------------------------------------------------ inspect
    def inspect(self, path: Path, kind: str, opts: Options) -> Inspection:
        p = probe(path, opts.timeout)
        fmt_name = p["format"].get("format_name", "")
        if _FORMAT_NAMES[kind] not in fmt_name:
            raise containers.MalformedError(f"container is '{fmt_name}', not {kind}")
        streams = p.get("streams", [])
        if not streams:
            raise containers.MalformedError("no media streams found")
        insp = Inspection(format=kind)
        insp.info.update(probe=p, path=str(path))

        for k, v in (p["format"].get("tags") or {}).items():
            insp.fields.append(Field(key=f"format:{k}", category=classify_av(k, str(v)), value=str(v)))
        roles = []
        for s in streams:
            role = _stream_role(s)
            roles.append(role)
            idx = s.get("index")
            for k, v in (s.get("tags") or {}).items():
                cat = classify_av(k, str(v))
                if role == "artwork" and k.lower() in ("comment", "title", "mimetype", "filename"):
                    cat = "technical"  # picture type ("Cover (front)") / attachment bookkeeping
                insp.fields.append(Field(key=f"stream:{idx}:{k}", category=cat, value=str(v)))
        for f in insp.fields:
            if f.category in ("technical", "disclosure", "provenance"):
                f.removable = False

        chapters = p.get("chapters", [])
        counts: dict[str, int] = {}
        for r in roles:
            counts[r] = counts.get(r, 0) + 1
        insp.info["roles"] = roles
        audio_kind = kind in AUDIO_KINDS
        if audio_kind and counts.get("video"):
            insp.unsupported_reason = "audio container with a real video track is outside the verified matrix"

        unpreservable = []
        for role in ("subtitle", "data", "attachment", "unknown"):
            if counts.get(role):
                unpreservable.append(f"{counts[role]} {role} stream(s)")
        if counts.get("artwork") and not audio_kind:
            unpreservable.append(f"{counts['artwork']} embedded cover/thumbnail stream(s)")
        if chapters:
            unpreservable.append(f"{len(chapters)} chapter(s)")
        if unpreservable:
            what = ", ".join(unpreservable)
            insp.block("streams", f"contains {what}; the verified stream-copy workflow keeps only audio/video "
                                  "(and audio artwork), so these would be dropped. Data streams can carry "
                                  "telemetry such as GPS. Re-run with --allow-stream-loss to drop them.")
            insp.flags.append("extra-content:" + what)
        if counts.get("data"):
            insp.unhandled.append("data stream(s) present: contents (possibly GPS telemetry) are not inspected")

        if counts.get("artwork") and audio_kind:
            n = counts["artwork"]
            insp.flags.append(f"artwork:{n}")
            hits = self._artwork_metadata(path, streams, opts)
            insp.info["artwork_meta"] = hits
            if hits and not opts.remove_artwork:
                insp.unhandled.append(f"embedded artwork carries its own image metadata ({', '.join(hits)}); "
                                      "use --remove-artwork or replace the cover image")
            insp.warnings.append(
                f"{n} embedded artwork picture(s): "
                + ("removed (--remove-artwork)" if opts.remove_artwork else "kept byte-for-byte")
            )

        bmff = kind in ("mp4", "mov", "m4a") and containers.bmff_top_level_c2pa(path)
        if bmff or containers.jumbf_c2pa_anywhere(path):
            insp.flags.append("provenance:c2pa")
            insp.block("provenance", "Content Credentials (C2PA/JUMBF) detected; remuxing would invalidate the "
                                     "signed manifest")
        return insp

    def _artwork_metadata(self, path: Path, streams: list[dict[str, Any]], opts: Options) -> list[str]:
        exe = tools.find_tool("ffmpeg")
        hits: list[str] = []
        for s in streams:
            if _stream_role(s) != "artwork":
                continue
            cp = tools.run([exe, "-nostdin", "-v", "error", "-i", tools.ffmpeg_file_arg(path), "-map",
                            f"0:{s['index']}", "-c", "copy", "-frames:v", "1", "-f", "image2pipe", "-"],
                           timeout=opts.timeout)
            blob = cp.stdout
            try:
                if blob.startswith(b"\xff\xd8"):
                    if b"Exif\x00\x00" in blob[:65536] or b"http://ns.adobe.com/xap/" in blob[:65536]:
                        hits.append(f"stream {s['index']}: EXIF/XMP")
                elif blob.startswith(containers.PNG_SIG):
                    st = containers.parse_png(blob)
                    if st.texts or b"eXIf" in blob:
                        hits.append(f"stream {s['index']}: PNG text/EXIF")
            except containers.MalformedError:
                hits.append(f"stream {s['index']}: unparseable picture")
        return hits

    # ------------------------------------------------------------ clean
    def _kept(self, insp: Inspection, opts: Options) -> list[int]:
        p = insp.info["probe"]
        kept = []
        for s, role in zip(p["streams"], insp.info["roles"], strict=True):
            if role in ("video", "audio"):
                kept.append(s["index"])
            elif role == "artwork" and insp.format in AUDIO_KINDS and not opts.remove_artwork:
                kept.append(s["index"])
        return kept

    def clean(self, src: Path, out: Path, kind: str, insp: Inspection, targeted: list[Field],
              policy: Policy, opts: Options, work: Path) -> list[str]:
        notes: list[str] = []
        kept = self._kept(insp, opts)
        insp.info["kept"] = kept
        newidx = {old: new for new, old in enumerate(kept)}
        targeted_keys = {f.key for f in targeted}
        argv = [tools.require("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-i", tools.ffmpeg_file_arg(src)]
        for i in kept:
            argv += ["-map", f"0:{i}"]
        argv += ["-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact"]

        needs_mdta = False
        lost: list[str] = []
        for f in insp.fields:
            if f.key in targeted_keys:
                continue
            parts = f.key.split(":", 2)
            tag = parts[-1]
            if tag.lower() in AV_STRUCTURAL or tag.lower().startswith("_statistics"):
                continue
            if "=" in tag or not tag:
                lost.append(f.key)
                continue
            if parts[0] == "format":
                argv += ["-metadata", f"{tag}={f.value}"]
                if "." in tag:
                    needs_mdta = True
            else:
                old = int(parts[1])
                if old not in newidx:
                    continue
                argv += [f"-metadata:s:{newidx[old]}", f"{tag}={f.value}"]
        if lost:
            notes.append(f"{len(lost)} retained tag(s) could not be re-written")

        if kind in ("mp4", "mov") and needs_mdta:
            argv += ["-movflags", "+use_metadata_tags"]
        if kind == "mp3":
            keep_id3 = any(not (f.key in targeted_keys) and f.key.startswith("format:") for f in insp.fields) \
                or any(insp.info["roles"][i] == "artwork" for i in kept)
            argv += ["-id3v2_version", "3" if keep_id3 else "0", "-write_id3v1", "0"]
        argv += ["-f", _MUXER[kind], tools.ffmpeg_file_arg(out)]
        cp = tools.run(argv, timeout=max(opts.timeout, 600))
        if cp.returncode != 0 or not out.exists():
            raise tools.ToolError(f"ffmpeg failed: {cp.err_text[:400]}")
        dropped = [r for i, r in enumerate(insp.info["roles"]) if insp.info["probe"]["streams"][i]["index"] not in kept]
        if dropped:
            notes.append("dropped streams: " + ", ".join(dropped))
        if insp.info["probe"].get("chapters"):
            notes.append(f"dropped {len(insp.info['probe']['chapters'])} chapter(s)")
        return notes

    # ------------------------------------------------------------ verify
    def _hashes(self, path: Path, maps: list[int] | None, opts: Options) -> list[str]:
        exe = tools.require("ffmpeg")
        argv = [exe, "-nostdin", "-v", "error", "-i", tools.ffmpeg_file_arg(path)]
        if maps is None:
            argv += ["-map", "0"]
        else:
            for i in maps:
                argv += ["-map", f"0:{i}"]
        argv += ["-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"]
        cp = tools.run(argv, timeout=max(opts.timeout, 600))
        if cp.returncode != 0:
            raise tools.ToolError(f"ffmpeg streamhash failed: {cp.err_text[:300]}")
        return [line.split(",", 1)[1] for line in cp.stdout.decode().splitlines() if "," in line]

    def integrity(self, orig: Inspection, out: Inspection, opts: Options) -> dict[str, dict[str, Any]]:
        po, pn = orig.info["probe"], out.info["probe"]
        kept = orig.info.get("kept") or [s["index"] for s in po["streams"]]
        by_idx = {s["index"]: s for s in po["streams"]}
        exp = [by_idx[i] for i in kept]
        got = pn["streams"]
        res: dict[str, dict[str, Any]] = {}
        layout_a = [(_stream_role(s), s.get("codec_name"), s.get("width"), s.get("height"), s.get("channels"),
                     s.get("sample_rate")) for s in exp]
        layout_b = [(_stream_role(s), s.get("codec_name"), s.get("width"), s.get("height"), s.get("channels"),
                     s.get("sample_rate")) for s in got]
        res["stream_layout"] = check(layout_a == layout_b,
                                     f"{len(got)} stream(s): " + ", ".join(f"{r}/{c}" for r, c, *_ in layout_b))
        try:
            ha = self._hashes(Path(orig.info["path"]), kept, opts)
            hb = self._hashes(Path(out.info["path"]), None, opts)
            res["packets_identical"] = check(ha == hb and len(ha) == len(kept),
                                             "per-stream packet SHA-256 matches (stream copy, no re-encode)")
        except tools.ToolError as exc:
            res["packets_identical"] = check(False, str(exc))
        da, db = _duration(po), _duration(pn)
        if da is None or db is None:
            res["duration"] = check(da == db, "duration unavailable")
        else:
            tol = max(_DURATION_TOL, da * 0.01)
            res["duration"] = check(abs(da - db) <= tol, f"{db:.3f}s (original {da:.3f}s)")
        rot_a = [_rotation(s) for s in exp if _stream_role(s) == "video"]
        rot_b = [_rotation(s) for s in got if _stream_role(s) == "video"]
        res["rotation"] = check(rot_a == rot_b, "rotation " + (", ".join(f"{r:g}°" for r in rot_b) or "n/a"))
        exe = tools.require("ffmpeg")
        cp = tools.run([exe, "-nostdin", "-v", "error", "-t", "2", "-i", tools.ffmpeg_file_arg(Path(out.info["path"])),
                        "-map", "0:a?", "-map", "0:V?", "-f", "null", "-"], timeout=max(opts.timeout, 300))
        res["decodes"] = check(cp.returncode == 0, "first 2 s decode without errors" if cp.returncode == 0
                               else cp.err_text[:200])
        return res

    def artifact_keys(self, out: Inspection) -> set[str]:
        keys = set()
        for f in out.fields:
            # Matroska always writes a bare muxer name (no version) even with +bitexact.
            if out.format == "mkv" and f.key == "format:encoder" and f.value == "Lavf":
                keys.add(f.key)
        return keys
