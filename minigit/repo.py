"""Finding the repository, HEAD and refs (branches are just files holding a sha)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from minigit.errors import MiniGitError
from minigit.fsutil import atomic_write

DEFAULT_BRANCH = "main"
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]+")
_SHORT_SHA_RE = re.compile(r"[0-9a-f]{4,40}")
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Repo:
    root: Path
    gitdir: Path


def init(path: Path) -> Repo:
    root = Path(path).resolve()
    gitdir = root / ".git"
    if gitdir.exists():
        raise MiniGitError(f"repository already exists at {gitdir}")
    for sub in ("objects", "refs/heads", "refs/tags"):
        (gitdir / sub).mkdir(parents=True)
    (gitdir / "HEAD").write_bytes(f"ref: refs/heads/{DEFAULT_BRANCH}\n".encode())
    filemode = "false" if os.name == "nt" else "true"
    (gitdir / "config").write_bytes(
        (
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            f"\tfilemode = {filemode}\n"
            "\tbare = false\n"
        ).encode()
    )
    return Repo(root, gitdir)


def _ceiling_dirs() -> set[Path]:
    raw = os.environ.get("GIT_CEILING_DIRECTORIES", "")
    return {Path(p).resolve() for p in raw.split(os.pathsep) if p}


def find_repo(start: Path | None = None) -> Repo:
    """Walk up from `start` until a folder with .git/HEAD is found."""
    current = Path(start or Path.cwd()).resolve()
    ceilings = _ceiling_dirs()
    for candidate in (current, *current.parents):
        if candidate != current and candidate in ceilings:
            break
        if (candidate / ".git" / "HEAD").is_file():
            return Repo(candidate, candidate / ".git")
    raise MiniGitError("not a git repository (or any of the parent directories): .git")


# --- refs ----------------------------------------------------------------


def read_ref(repo: Repo, refname: str) -> str | None:
    path = repo.gitdir / refname
    if path.is_file():
        return path.read_text().strip()
    packed = repo.gitdir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            sha, _, name = line.partition(" ")
            if name == refname:
                return sha
    return None


def update_ref(repo: Repo, refname: str, sha: str) -> None:
    atomic_write(repo.gitdir / refname, f"{sha}\n".encode())


def is_valid_branch_name(name: str) -> bool:
    return not (
        not _BRANCH_RE.fullmatch(name)
        or name.startswith(("-", "/"))
        or name.endswith(("/", ".lock", "."))
        or ".." in name
        or "//" in name
    )


def branch_ref(name: str) -> str:
    return f"refs/heads/{name}"


def list_branches(repo: Repo) -> tuple[str, ...]:
    heads = repo.gitdir / "refs" / "heads"
    names = {p.relative_to(heads).as_posix() for p in heads.rglob("*") if p.is_file()}
    packed = repo.gitdir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            _, _, name = line.partition(" ")
            if name.startswith("refs/heads/"):
                names.add(name.removeprefix("refs/heads/"))
    return tuple(sorted(names))


# --- HEAD ----------------------------------------------------------------


def _read_head(repo: Repo) -> str:
    return (repo.gitdir / "HEAD").read_text().strip()


def current_branch(repo: Repo) -> str | None:
    """Branch name HEAD points to, or None when HEAD is detached on a commit."""
    head = _read_head(repo)
    if head.startswith("ref: refs/heads/"):
        return head.removeprefix("ref: refs/heads/")
    return None


def head_commit(repo: Repo) -> str | None:
    """Commit sha of HEAD, or None in a new repo with no commits yet."""
    head = _read_head(repo)
    if head.startswith("ref: "):
        return read_ref(repo, head.removeprefix("ref: "))
    return head


def set_head_to_branch(repo: Repo, name: str) -> None:
    atomic_write(repo.gitdir / "HEAD", f"ref: {branch_ref(name)}\n".encode())


def set_head_detached(repo: Repo, sha: str) -> None:
    atomic_write(repo.gitdir / "HEAD", f"{sha}\n".encode())


def move_head(repo: Repo, sha: str) -> None:
    """Advance whatever HEAD points to: the current branch, or HEAD itself if detached."""
    branch = current_branch(repo)
    if branch is None:
        set_head_detached(repo, sha)
    else:
        update_ref(repo, branch_ref(branch), sha)


# --- revisions -----------------------------------------------------------


def resolve_revision(repo: Repo, rev: str) -> str:
    """Turn "HEAD", a branch name, a full sha or a short sha into a full sha."""
    if rev == "HEAD":
        sha = head_commit(repo)
        if sha is None:
            raise MiniGitError("HEAD does not point to a commit yet")
        return sha
    # Only valid branch names reach the file system: "../../config" must not be read as a ref.
    branch_sha = read_ref(repo, branch_ref(rev)) if is_valid_branch_name(rev) else None
    if branch_sha:
        return branch_sha
    if _SHORT_SHA_RE.fullmatch(rev):
        matches = _objects_with_prefix(repo, rev)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise MiniGitError(f"short sha {rev} is ambiguous")
    raise MiniGitError(f"unknown revision: {rev}")


def _objects_with_prefix(repo: Repo, prefix: str) -> list[str]:
    folder = repo.gitdir / "objects" / prefix[:2]
    if not folder.is_dir():
        return []
    return [
        prefix[:2] + p.name
        for p in folder.iterdir()
        if p.name.startswith(prefix[2:]) and _FULL_SHA_RE.fullmatch(prefix[:2] + p.name)
    ]
