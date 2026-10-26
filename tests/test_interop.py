"""The real proof: the official git must read what minigit writes, and vice versa."""

import os
import shutil
import subprocess

import pytest

from conftest import FIXED_TIME, write
from minigit import commands
from minigit.repo import head_commit

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def run_git(repo, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_COMMITTER_NAME": "Real Git",
        "GIT_COMMITTER_EMAIL": "real@example.com",
    }
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo.root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_git_accepts_repository_written_by_minigit(repo):
    write(repo, "README.md", "# demo\n")
    write(repo, "src/main.py", "print('hi')\n")
    commands.add(repo, [repo.root])
    commands.commit(repo, "first", when=FIXED_TIME)
    write(repo, "src/main.py", "print('bye')\n")
    commands.add(repo, [repo.root / "src"])
    sha = commands.commit(repo, "second", when=FIXED_TIME)

    run_git(repo, "fsck", "--strict")
    assert run_git(repo, "rev-parse", "HEAD").strip() == sha
    assert run_git(repo, "log", "--format=%s").splitlines() == ["second", "first"]
    assert run_git(repo, "status", "--porcelain") == ""


def test_minigit_reads_commit_made_by_git(repo):
    write(repo, "notes.txt", "from real git\n")
    run_git(repo, "add", "notes.txt")
    run_git(repo, "commit", "-q", "-m", "made by git")

    ((sha, commit),) = commands.log(repo)

    assert sha == head_commit(repo)
    assert commit.message == "made by git\n"
    assert commands.status(repo).is_clean
