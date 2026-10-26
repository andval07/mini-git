"""The porcelain: add, commit, log, status, branch, checkout, cat-file."""

import os
import stat
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from minigit.errors import MiniGitError
from minigit.identity import signature
from minigit.index import IndexEntry, entry_from_stat, index_path, read_index, write_index
from minigit.objects import (
    MODE_EXEC,
    MODE_FILE,
    MODE_TREE,
    Commit,
    TreeEntry,
    hash_object,
    parse_commit,
    parse_tree,
    read_object,
    serialize_commit,
    serialize_tree,
    write_object,
)
from minigit.repo import (
    is_valid_branch_name,
    Repo,
    branch_ref,
    current_branch,
    head_commit,
    move_head,
    read_ref,
    resolve_revision,
    set_head_detached,
    set_head_to_branch,
    update_ref,
)



# --- helpers -------------------------------------------------------------


def _rel(repo: Repo, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.root).as_posix()
    except ValueError as exc:
        raise MiniGitError(f"'{path}' is outside repository at '{repo.root}'") from exc


def _file_mode(st: os.stat_result) -> int:
    return int(MODE_EXEC if st.st_mode & stat.S_IXUSR and os.name != "nt" else MODE_FILE, 8)


def _working_files(repo: Repo, folder: Path) -> list[str]:
    """Every file under `folder`, as repo-relative posix paths, skipping .git."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        found += [_rel(repo, Path(dirpath) / name) for name in filenames]
    return sorted(found)


def _stage_file(repo: Repo, rel: str) -> IndexEntry:
    path = repo.root / rel
    st = path.stat()
    sha = write_object(repo.gitdir, "blob", path.read_bytes())
    return entry_from_stat(rel, sha, _file_mode(st), st)


def read_commit(gitdir: Path, sha: str) -> Commit:
    obj_type, data = read_object(gitdir, sha)
    if obj_type != "commit":
        raise MiniGitError(f"{sha} is a {obj_type}, not a commit")
    return parse_commit(data)


def flatten_tree(gitdir: Path, tree_sha: str, prefix: str = "") -> dict[str, TreeEntry]:
    """Walk a tree recursively: {"src/app.py": TreeEntry(mode, "src/app.py", blob sha)}."""
    obj_type, data = read_object(gitdir, tree_sha)
    if obj_type != "tree":
        raise MiniGitError(f"{tree_sha} is a {obj_type}, not a tree")
    files: dict[str, TreeEntry] = {}
    for entry in parse_tree(data):
        path = prefix + entry.name
        if entry.is_tree:
            files |= flatten_tree(gitdir, entry.sha, path + "/")
        else:
            files[path] = TreeEntry(entry.mode, path, entry.sha)
    return files


def _head_files(repo: Repo) -> dict[str, TreeEntry]:
    sha = head_commit(repo)
    return flatten_tree(repo.gitdir, read_commit(repo.gitdir, sha).tree) if sha else {}


# --- add -----------------------------------------------------------------


def add(repo: Repo, paths: list[Path]) -> None:
    staged = {e.path: e for e in read_index(repo.gitdir)}
    for raw in paths:
        path = Path(raw).resolve()
        rel = _rel(repo, path)
        if rel.split("/")[0].lower() == ".git":
            raise MiniGitError(f"'{raw}' is inside the .git folder and cannot be added")
        if path.is_file():
            staged[rel] = _stage_file(repo, rel)
        elif path.is_dir():
            prefix = "" if rel == "." else rel + "/"
            present = _working_files(repo, path)
            # Files that vanished from disk get staged as deletions.
            staged = {p: e for p, e in staged.items() if not p.startswith(prefix) or p in present}
            staged |= {p: _stage_file(repo, p) for p in present}
        elif rel in staged:
            staged = {p: e for p, e in staged.items() if p != rel}
        else:
            raise MiniGitError(f"pathspec '{raw}' did not match any files")
    write_index(repo.gitdir, tuple(staged.values()))


# --- commit --------------------------------------------------------------


def write_tree(gitdir: Path, entries: tuple[IndexEntry, ...]) -> str:
    """Turn the flat index ("a/b/c.txt") into nested tree objects; return the root sha."""
    files: list[TreeEntry] = []
    folders: dict[str, list[IndexEntry]] = {}
    for e in entries:
        head, sep, rest = e.path.partition("/")
        if sep:
            folders.setdefault(head, []).append(replace(e, path=rest))
        else:
            files.append(TreeEntry(f"{e.mode:o}", e.path, e.sha))
    subtrees = [
        TreeEntry(MODE_TREE, name, write_tree(gitdir, tuple(children)))
        for name, children in folders.items()
    ]
    return write_object(gitdir, "tree", serialize_tree(files + subtrees))


def commit(repo: Repo, message: str, when: datetime | None = None) -> str:
    if not message.strip():
        raise MiniGitError("aborting commit due to empty commit message")
    entries = read_index(repo.gitdir)
    if not entries:
        raise MiniGitError("nothing to commit (use 'add' to stage files)")

    tree = write_tree(repo.gitdir, entries)
    parent = head_commit(repo)
    if parent and read_commit(repo.gitdir, parent).tree == tree:
        raise MiniGitError("nothing to commit, working tree clean")

    new = Commit(
        tree=tree,
        parents=(parent,) if parent else (),
        author=signature("AUTHOR", repo.gitdir, when),
        committer=signature("COMMITTER", repo.gitdir, when),
        message=message.strip() + "\n",
    )
    sha = write_object(repo.gitdir, "commit", serialize_commit(new))
    move_head(repo, sha)
    return sha


# --- log -----------------------------------------------------------------


def log(repo: Repo) -> tuple[tuple[str, Commit], ...]:
    """History from HEAD, newest first, following the first parent."""
    sha = head_commit(repo)
    if sha is None:
        raise MiniGitError("your current branch does not have any commits yet")
    history = []
    while sha:
        current = read_commit(repo.gitdir, sha)
        history.append((sha, current))
        sha = current.parents[0] if current.parents else None
    return tuple(history)


# --- status --------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    branch: str | None
    head: str | None
    staged: tuple[tuple[str, str], ...]
    unstaged: tuple[tuple[str, str], ...]
    untracked: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not (self.staged or self.unstaged or self.untracked)


def _diff(old: dict[str, str], new: dict[str, str]) -> tuple[tuple[str, str], ...]:
    changes = []
    for path in sorted(old.keys() | new.keys()):
        if path not in old:
            changes.append(("new file", path))
        elif path not in new:
            changes.append(("deleted", path))
        elif old[path] != new[path]:
            changes.append(("modified", path))
    return tuple(changes)


def _worktree_sha(repo: Repo, entry: IndexEntry, index_mtime_ns: int) -> str | None:
    """Blob sha of the file on disk now, or None if it was deleted.

    Fast path: if size and mtime match the index, trust the stored sha. The exception
    is a "racy" entry (file modified in the same instant the index was written):
    then the timestamps cannot be trusted and we re-hash, exactly like git does.
    """
    path = repo.root / entry.path
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    entry_mtime_ns = entry.mtime_s * 1_000_000_000 + entry.mtime_ns
    unchanged_stat = st.st_size == entry.size and st.st_mtime_ns == entry_mtime_ns
    is_racy = entry_mtime_ns >= index_mtime_ns
    if unchanged_stat and not is_racy:
        return entry.sha
    return hash_object("blob", path.read_bytes())


def status(repo: Repo) -> Status:
    entries = read_index(repo.gitdir)
    index_file = index_path(repo.gitdir)
    index_mtime_ns = index_file.stat().st_mtime_ns if index_file.exists() else 0

    head = {p: e.sha for p, e in _head_files(repo).items()}
    staged = {e.path: e.sha for e in entries}
    on_disk = {
        e.path: sha
        for e in entries
        if (sha := _worktree_sha(repo, e, index_mtime_ns)) is not None
    }
    untracked = tuple(p for p in _working_files(repo, repo.root) if p not in staged)
    return Status(
        branch=current_branch(repo),
        head=head_commit(repo),
        staged=_diff(head, staged),
        unstaged=tuple(c for c in _diff(staged, on_disk) if c[0] != "new file"),
        untracked=untracked,
    )


# --- branch --------------------------------------------------------------


def _check_branch_name(name: str) -> None:
    if not is_valid_branch_name(name):
        raise MiniGitError(f"invalid branch name: '{name}'")


def branch(repo: Repo, name: str) -> None:
    _check_branch_name(name)
    sha = head_commit(repo)
    if sha is None:
        raise MiniGitError("no commits yet: make a first commit before creating branches")
    if read_ref(repo, branch_ref(name)):
        raise MiniGitError(f"a branch named '{name}' already exists")
    update_ref(repo, branch_ref(name), sha)


# --- checkout ------------------------------------------------------------


def checkout(repo: Repo, target: str) -> None:
    is_branch = read_ref(repo, branch_ref(target)) is not None
    sha = resolve_revision(repo, target)
    wanted = flatten_tree(repo.gitdir, read_commit(repo.gitdir, sha).tree)

    current = status(repo)
    if current.staged or current.unstaged:
        raise MiniGitError("you have uncommitted changes: commit them before checkout")
    tracked = {e.path: e for e in read_index(repo.gitdir)}
    clashes = [p for p in wanted if p not in tracked and (repo.root / p).exists()]
    if clashes:
        raise MiniGitError(f"checkout would overwrite untracked files: {', '.join(clashes)}")

    for path in tracked.keys() - wanted.keys():
        _remove_file(repo, path)
    new_entries = []
    for path, entry in wanted.items():
        if path not in tracked or tracked[path].sha != entry.sha:
            _write_file(repo, path, entry)
        st = (repo.root / path).stat()
        new_entries.append(entry_from_stat(path, entry.sha, int(entry.mode, 8), st))
    write_index(repo.gitdir, tuple(new_entries))

    if is_branch:
        set_head_to_branch(repo, target)
    else:
        set_head_detached(repo, sha)


def _write_file(repo: Repo, path: str, entry: TreeEntry) -> None:
    _, data = read_object(repo.gitdir, entry.sha)
    dest = repo.root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if entry.mode == MODE_EXEC and os.name != "nt":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _remove_file(repo: Repo, path: str) -> None:
    target = repo.root / path
    target.unlink(missing_ok=True)
    folder = target.parent
    while folder != repo.root and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


# --- cat-file ------------------------------------------------------------


def cat_file(repo: Repo, rev: str) -> str:
    sha = resolve_revision(repo, rev)
    obj_type, data = read_object(repo.gitdir, sha)
    if obj_type != "tree":
        return data.decode(errors="replace")
    return "\n".join(
        f"{e.mode.zfill(6)} {'tree' if e.is_tree else 'blob'} {e.sha}\t{e.name}"
        for e in parse_tree(data)
    )
