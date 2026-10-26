"""Command-line entry point: `minigit <command>` or `python -m minigit <command>`."""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minigit import commands
from minigit.errors import MiniGitError
from minigit.objects import Commit, hash_object, write_object
from minigit.repo import current_branch, find_repo, head_commit, init, list_branches


def _format_date(signature: str) -> str:
    """Turn "... 1792749600 +0200" into "Fri Oct 23 12:00:00 2026 +0200"."""
    _, timestamp, offset = signature.rsplit(" ", 2)
    sign = -1 if offset.startswith("-") else 1
    delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])) * sign
    moment = datetime.fromtimestamp(int(timestamp), timezone(delta))
    return moment.strftime("%a %b %d %H:%M:%S %Y ") + offset


def _print_commit(sha: str, commit: Commit) -> None:
    who = commit.author.rsplit(" ", 2)[0]
    print(f"commit {sha}")
    if len(commit.parents) > 1:
        print("Merge: " + " ".join(p[:7] for p in commit.parents))
    print(f"Author: {who}")
    print(f"Date:   {_format_date(commit.author)}")
    print()
    for line in commit.message.rstrip("\n").split("\n"):
        print(f"    {line}")
    print()


def cmd_init(args: argparse.Namespace) -> None:
    repo = init(Path(args.path))
    print(f"Initialized empty Git repository in {repo.gitdir}")


def cmd_add(args: argparse.Namespace) -> None:
    commands.add(find_repo(), [Path(p) for p in args.paths])


def cmd_commit(args: argparse.Namespace) -> None:
    repo = find_repo()
    sha = commands.commit(repo, args.message)
    where = current_branch(repo) or "detached HEAD"
    print(f"[{where} {sha[:7]}] {args.message.strip().splitlines()[0]}")


def cmd_log(args: argparse.Namespace) -> None:
    for sha, commit in commands.log(find_repo()):
        _print_commit(sha, commit)


def cmd_status(args: argparse.Namespace) -> None:
    st = commands.status(find_repo())
    print(f"On branch {st.branch}" if st.branch else f"HEAD detached at {(st.head or '')[:7]}")
    sections = [
        ("Changes to be committed:", st.staged),
        ("Changes not staged for commit:", st.unstaged),
    ]
    for title, changes in sections:
        if changes:
            print(f"\n{title}")
            for kind, path in changes:
                print(f"\t{kind + ':':<12}{path}")
    if st.untracked:
        print("\nUntracked files:")
        for path in st.untracked:
            print(f"\t{path}")
    if st.is_clean:
        print("nothing to commit, working tree clean")


def cmd_branch(args: argparse.Namespace) -> None:
    repo = find_repo()
    if args.name:
        commands.branch(repo, args.name)
        return
    active = current_branch(repo)
    for name in list_branches(repo):
        print(f"{'*' if name == active else ' '} {name}")


def cmd_checkout(args: argparse.Namespace) -> None:
    repo = find_repo()
    commands.checkout(repo, args.target)
    branch = current_branch(repo)
    if branch:
        print(f"Switched to branch '{branch}'")
    else:
        print(f"HEAD is now at {(head_commit(repo) or '')[:7]}")


def cmd_cat_file(args: argparse.Namespace) -> None:
    print(commands.cat_file(find_repo(), args.object))


def cmd_hash_object(args: argparse.Namespace) -> None:
    data = Path(args.file).read_bytes()
    sha = write_object(find_repo().gitdir, "blob", data) if args.write else hash_object("blob", data)
    print(sha)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minigit", description="A small Git clone.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create an empty repository")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", help="stage file contents")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("commit", help="record staged changes")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_commit)

    sub.add_parser("log", help="show commit history").set_defaults(func=cmd_log)
    sub.add_parser("status", help="show working tree status").set_defaults(func=cmd_status)

    p = sub.add_parser("branch", help="list branches, or create one")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("checkout", help="switch to a branch or commit")
    p.add_argument("target")
    p.set_defaults(func=cmd_checkout)

    p = sub.add_parser("cat-file", help="print an object")
    p.add_argument("-p", dest="pretty", action="store_true", help="accepted for git compatibility")
    p.add_argument("object")
    p.set_defaults(func=cmd_cat_file)

    p = sub.add_parser("hash-object", help="compute a blob sha")
    p.add_argument("-w", dest="write", action="store_true", help="also store the object")
    p.add_argument("file")
    p.set_defaults(func=cmd_hash_object)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except MiniGitError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"fatal: {exc.strerror}: {exc.filename}", file=sys.stderr)
        return 1
    return 0
