"""Filesystem safety: hashing, temporary work dirs, and no-overwrite publishing."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

WORKDIR_PREFIX = ".veriscrub-"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def is_regular_file_nofollow(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


@contextmanager
def workdir(near: Path) -> Iterator[Path]:
    """A private temp directory on the same filesystem as ``near`` (for atomic publish)."""
    near.mkdir(parents=True, exist_ok=True)
    try:
        d = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=str(near)))
    except OSError:
        d = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def candidate_names(dest: Path) -> Iterator[Path]:
    yield dest
    for i in range(1, 10000):
        yield dest.with_name(f"{dest.stem}-{i}{dest.suffix}")


def publish(tmp: Path, dest: Path, on_collision: str = "rename", reserved: set[str] | None = None) -> Path | None:
    """Move a verified temp file to ``dest`` without ever overwriting an existing file.

    Returns the final path, or None when ``on_collision == 'skip'`` and the
    name was taken. Raises FileExistsError for ``on_collision == 'fail'``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    reserved = reserved if reserved is not None else set()
    names = candidate_names(dest) if on_collision == "rename" else iter([dest])
    for cand in names:
        key = os.path.normcase(str(cand.resolve()))
        if key in reserved:
            continue
        if _link_no_overwrite(tmp, cand):
            reserved.add(key)
            return cand
        if on_collision == "skip":
            return None
        if on_collision == "fail":
            raise FileExistsError(f"output exists: {cand}")
    raise FileExistsError(f"no free output name near {dest}")


def _link_no_overwrite(src: Path, dst: Path) -> bool:
    """Atomically create ``dst`` from ``src``; False if ``dst`` exists."""
    if os.name == "nt":
        try:
            os.rename(src, dst)  # Windows rename never replaces an existing file
            return True
        except FileExistsError:
            return False
    try:
        os.link(src, dst)
        os.unlink(src)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP, errno.EMLINK):
            raise
    # Filesystems without hard links: exclusive create + copy.
    try:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        try:
            os.unlink(dst)
        finally:
            raise
    os.unlink(src)
    return True


def read_limited(path: Path, limit: int) -> bytes:
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"file is larger than the {limit // (1 << 20)} MiB limit for this format")
    with open(path, "rb") as fh:
        return fh.read()


def scan_for_values(path: Path, needles: list[bytes], chunk: int = 1 << 20) -> set[bytes]:
    """Return which byte strings occur anywhere in the file (streaming, overlap-safe)."""
    needles = [n for n in needles if n]
    if not needles:
        return set()
    overlap = max(len(n) for n in needles) - 1
    found: set[bytes] = set()
    tail = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            buf = tail + block
            for n in needles:
                if n not in found and n in buf:
                    found.add(n)
            if len(found) == len(needles):
                break
            tail = buf[-overlap:] if overlap > 0 else b""
    return found


def value_needles(values: list[str], min_len: int = 6) -> dict[bytes, str]:
    """Byte encodings to search for removed values (UTF-8 and UTF-16)."""
    out: dict[bytes, str] = {}
    for v in values:
        v = v.strip()
        if len(v) < min_len or v.replace(".", "").replace("-", "").replace(" ", "").isdigit():
            continue
        v = v[:200]
        for enc in ("utf-8", "utf-16-be", "utf-16-le"):
            try:
                out[v.encode(enc)] = v
            except UnicodeEncodeError:
                pass
    return out
