"""The staging area (.git/index), in git's binary format version 2.

Layout: 12-byte header ("DIRC", version, entry count), the entries sorted by path,
then a SHA-1 of everything before it. Each entry keeps the file's stat data
(times, size, mode) next to the blob sha, so `status` can skip re-hashing files
that did not change.
"""

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from minigit.errors import MiniGitError
from minigit.fsutil import atomic_write
from minigit.objects import check_safe_name

_HEADER = struct.Struct(">4sII")
# ctime s/ns, mtime s/ns, dev, ino, mode, uid, gid, size, sha(20), flags
_ENTRY = struct.Struct(">10I20sH")
_EXTENDED_FLAG = 0x4000
_NAME_MASK = 0x0FFF
_U32 = 0xFFFFFFFF


@dataclass(frozen=True)
class IndexEntry:
    path: str
    sha: str
    mode: int
    size: int
    mtime_s: int
    mtime_ns: int = 0
    ctime_s: int = 0
    ctime_ns: int = 0
    dev: int = 0
    ino: int = 0
    uid: int = 0
    gid: int = 0


def entry_from_stat(path: str, sha: str, mode: int, st: os.stat_result) -> IndexEntry:
    return IndexEntry(
        path=path,
        sha=sha,
        mode=mode,
        size=st.st_size & _U32,
        mtime_s=int(st.st_mtime) & _U32,
        mtime_ns=st.st_mtime_ns % 1_000_000_000,
        ctime_s=int(st.st_ctime) & _U32,
        ctime_ns=st.st_ctime_ns % 1_000_000_000,
        dev=st.st_dev & _U32,
        ino=st.st_ino & _U32,
        uid=st.st_uid & _U32,
        gid=st.st_gid & _U32,
    )


def index_path(gitdir: Path) -> Path:
    return gitdir / "index"


def read_index(gitdir: Path) -> tuple[IndexEntry, ...]:
    path = index_path(gitdir)
    if not path.exists():
        return ()
    data = path.read_bytes()
    if len(data) < _HEADER.size + 20:
        raise MiniGitError("index file is too short")
    body, checksum = data[:-20], data[-20:]
    if hashlib.sha1(body).digest() != checksum:
        raise MiniGitError("index checksum mismatch: the index file is corrupted")

    signature, version, count = _HEADER.unpack_from(body)
    if signature != b"DIRC" or version not in (2, 3):
        raise MiniGitError(f"unsupported index (signature {signature!r}, version {version})")

    entries = []
    pos = _HEADER.size
    try:
        for _ in range(count):
            entry, pos = _read_entry(body, pos, version)
            for part in entry.path.split("/"):
                check_safe_name(part)
            entries.append(entry)
    except (struct.error, ValueError, UnicodeDecodeError) as exc:
        raise MiniGitError(f"index file is corrupted: {exc}") from exc
    # Anything after the entries is an optional extension (e.g. git's TREE cache): ignored.
    return tuple(entries)


def _read_entry(body: bytes, start: int, version: int) -> tuple[IndexEntry, int]:
    (ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size, sha, flags) = (
        _ENTRY.unpack_from(body, start)
    )
    name_start = start + _ENTRY.size
    if version >= 3 and flags & _EXTENDED_FLAG:
        name_start += 2
    name_end = body.index(b"\0", name_start)
    path = body[name_start:name_end].decode()
    # Entries are padded with 1-8 NUL bytes so each one is a multiple of 8 bytes long.
    entry_len = name_end - start
    next_pos = start + ((entry_len + 8) & ~7)
    entry = IndexEntry(
        path=path,
        sha=sha.hex(),
        mode=mode,
        size=size,
        mtime_s=mtime_s,
        mtime_ns=mtime_ns,
        ctime_s=ctime_s,
        ctime_ns=ctime_ns,
        dev=dev,
        ino=ino,
        uid=uid,
        gid=gid,
    )
    return entry, next_pos


def write_index(gitdir: Path, entries: tuple[IndexEntry, ...]) -> None:
    ordered = sorted(entries, key=lambda e: e.path.encode())
    parts = [_HEADER.pack(b"DIRC", 2, len(ordered))]
    for e in ordered:
        name = e.path.encode()
        fixed = _ENTRY.pack(
            e.ctime_s,
            e.ctime_ns,
            e.mtime_s,
            e.mtime_ns,
            e.dev,
            e.ino,
            e.mode,
            e.uid,
            e.gid,
            e.size,
            bytes.fromhex(e.sha),
            min(len(name), _NAME_MASK),
        )
        raw = fixed + name
        padding = ((len(raw) + 8) & ~7) - len(raw)
        parts.append(raw + b"\0" * padding)
    body = b"".join(parts)
    atomic_write(index_path(gitdir), body + hashlib.sha1(body).digest())
