"""The object database: blobs, trees and commits, stored exactly like git does.

Every object is saved as zlib("<type> <size>\\0<content>") under
.git/objects/<first 2 hex chars>/<other 38>, and its name is the SHA-1 of the
uncompressed bytes. Same content -> same name, so storage is deduplicated for free.
"""

import hashlib
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from minigit.errors import MiniGitError
from minigit.fsutil import atomic_write

OBJECT_TYPES = frozenset({"blob", "tree", "commit"})
MODE_FILE = "100644"
MODE_EXEC = "100755"
MODE_TREE = "40000"

_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _frame(obj_type: str, data: bytes) -> bytes:
    if obj_type not in OBJECT_TYPES:
        raise MiniGitError(f"unsupported object type: {obj_type}")
    return f"{obj_type} {len(data)}".encode() + b"\0" + data


def _object_path(gitdir: Path, sha: str) -> Path:
    return gitdir / "objects" / sha[:2] / sha[2:]


def hash_object(obj_type: str, data: bytes) -> str:
    return hashlib.sha1(_frame(obj_type, data)).hexdigest()


def write_object(gitdir: Path, obj_type: str, data: bytes) -> str:
    framed = _frame(obj_type, data)
    sha = hashlib.sha1(framed).hexdigest()
    path = _object_path(gitdir, sha)
    if not path.exists():  # already stored: content-addressing makes this safe to skip
        atomic_write(path, zlib.compress(framed))
    return sha


def read_object(gitdir: Path, sha: str) -> tuple[str, bytes]:
    if not _SHA_RE.fullmatch(sha):
        raise MiniGitError(f"invalid object name: {sha}")
    path = _object_path(gitdir, sha)
    if not path.is_file():
        raise MiniGitError(f"object {sha} not found")
    try:
        raw = zlib.decompress(path.read_bytes())
    except zlib.error as exc:
        raise MiniGitError(f"object {sha} is corrupted: {exc}") from exc

    header, sep, data = raw.partition(b"\0")
    obj_type, _, size = header.decode(errors="replace").partition(" ")
    if not sep or obj_type not in OBJECT_TYPES or size != str(len(data)):
        raise MiniGitError(f"object {sha} has a bad header")
    return obj_type, data


# --- trees ---------------------------------------------------------------


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    name: str
    sha: str

    @property
    def is_tree(self) -> bool:
        return self.mode == MODE_TREE

    @property
    def sort_key(self) -> bytes:
        # git sorts folders as if their name ended with "/"
        return (self.name + "/" if self.is_tree else self.name).encode()


def serialize_tree(entries: list[TreeEntry]) -> bytes:
    """Each entry is "<mode> <name>\\0<20 raw bytes of sha>", sorted git-style."""
    return b"".join(
        f"{e.mode} {e.name}".encode() + b"\0" + bytes.fromhex(e.sha)
        for e in sorted(entries, key=lambda e: e.sort_key)
    )


def check_safe_name(name: str) -> None:
    """Reject names that could write outside the work tree or into .git on checkout."""
    if name in ("", ".", "..") or "/" in name or "\\" in name or name.lower() == ".git":
        raise MiniGitError(f"unsafe path component in tree: {name!r}")


def parse_tree(data: bytes) -> tuple[TreeEntry, ...]:
    entries = []
    pos = 0
    try:
        while pos < len(data):
            space = data.index(b" ", pos)
            nul = data.index(b"\0", space)
            mode = data[pos:space].decode()
            name = data[space + 1 : nul].decode()
            raw_sha = data[nul + 1 : nul + 21]
            if len(raw_sha) != 20:
                raise ValueError("truncated sha")
            check_safe_name(name)
            entries.append(TreeEntry(mode, name, raw_sha.hex()))
            pos = nul + 21
    except (ValueError, UnicodeDecodeError) as exc:
        raise MiniGitError(f"corrupted tree object: {exc}") from exc
    return tuple(entries)


# --- commits -------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    tree: str
    parents: tuple[str, ...]
    author: str
    committer: str
    message: str


def serialize_commit(commit: Commit) -> bytes:
    lines = [f"tree {commit.tree}"]
    lines += [f"parent {p}" for p in commit.parents]
    lines += [f"author {commit.author}", f"committer {commit.committer}"]
    return ("\n".join(lines) + "\n\n" + commit.message).encode()


def parse_commit(data: bytes) -> Commit:
    header, _, message = data.decode(errors="replace").partition("\n\n")
    fields: dict[str, str] = {}
    parents: list[str] = []
    for line in header.split("\n"):
        if line.startswith(" "):
            continue  # continuation of a multi-line header (e.g. gpgsig): not needed here
        key, _, value = line.partition(" ")
        if key == "parent":
            parents.append(value)
        else:
            fields.setdefault(key, value)

    missing = {"tree", "author", "committer"} - fields.keys()
    if missing:
        raise MiniGitError(f"commit is missing: {', '.join(sorted(missing))}")
    return Commit(
        tree=fields["tree"],
        parents=tuple(parents),
        author=fields["author"],
        committer=fields["committer"],
        message=message,
    )
