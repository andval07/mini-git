# minigit

A small Git clone in pure Python (standard library only).

minigit writes repositories in the **same on-disk format as Git**. The real
`git` can read them: `git fsck --strict`, `git log` and `git status` all work on
a repo built only with minigit. The reverse works too: minigit reads commits
made by the real `git`. The test suite checks this on every run.

## What it does

| Command | What it does |
|---|---|
| `init [path]` | creates `.git/` with `objects/`, `refs/heads/` and `HEAD` |
| `add <path>...` | stores file contents as blobs and updates the binary index |
| `commit -m <msg>` | turns the index into trees and writes a commit |
| `log` | prints the history from HEAD |
| `status` | shows staged, unstaged and untracked files |
| `branch [name]` | lists branches or creates one |
| `checkout <branch\|sha>` | switches branch, or detaches HEAD on a commit |
| `cat-file -p <object>` | prints a blob, tree or commit |
| `hash-object [-w] <file>` | prints a file's blob SHA-1 |

## Demo

```bash
python -m venv .venv && .venv/bin/pip install -e .   # Windows: .venv\Scripts\pip
minigit init demo && cd demo
echo hello > hello.txt
minigit add .
minigit commit -m "first commit"
minigit branch feature
minigit checkout feature
minigit log

git fsck --strict    # the real git accepts the repository
git log --oneline
```

## How it works

```
working tree --add--> index (.git/index) --commit--> tree + commit objects
                                                         |
                                     refs/heads/main <---+   HEAD -> refs/heads/main
```

- **Objects** (`minigit/objects.py`): each object is stored as
  `zlib("<type> <size>\0<content>")` at `.git/objects/ab/cdef...`. Its name is the
  SHA-1 of the uncompressed bytes, so identical content is stored only once.
- **Trees**: one entry per file or folder, written as `<mode> <name>\0<20-byte sha>`.
  Entries are sorted the way Git sorts them, with folders compared as `name/`.
  If the order is wrong, the hash changes and `git fsck` reports an error.
- **Commits**: plain text with `tree`, `parent`, `author` and `committer`
  headers, then the message.
- **Index** (`minigit/index.py`): Git's binary format version 2, with a `DIRC`
  header, sorted entries with stat data, and a SHA-1 trailer.
- **Status** checks whether a file changed using its size and mtime, so it does
  not re-hash every file. It also handles the "racy git" case, like Git does:
  when a file changed in the same instant the index was written, the timestamp
  can't be trusted, so minigit re-hashes the file.
- **Refs**: a branch is a file containing a commit SHA. `HEAD` holds either
  `ref: refs/heads/<name>` or a SHA (detached HEAD).
- Files are written atomically: minigit writes a temp file, then renames it over
  the target.

## Tests

```bash
pip install pytest pytest-cov
pytest --cov=minigit
```

There are 46 tests, with about 89% line coverage. They cover:

- known hashes, checked against `git hash-object`
- tree sort order
- an index round-trip, and an index corrupted on purpose
- every command, including the errors
- an interop test against the real `git`
- hostile input: tree entries like `..` or `.git`, revisions like `../../config`,
  truncated trees and index files

## Limits (on purpose)

- No merge, rebase, diff, remote, or packfiles. minigit reads `packed-refs`, but
  not packed objects.
- No `.gitignore`.
- No symlinks or submodules.
- `status` lists untracked files one by one. It does not collapse them per folder.
- `checkout` refuses to run when there are uncommitted changes. Git can sometimes
  carry the changes over to the other branch.

## Layout

```
minigit/
  objects.py   object store, trees, commits
  index.py     binary staging area
  repo.py      repo discovery, HEAD, refs, revision parsing
  identity.py  author / committer signature
  commands.py  add, commit, log, status, branch, checkout, cat-file
  cli.py       argparse entry point
tests/         pytest suite, including interop with the real git
```
