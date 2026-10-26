from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minigit.repo import Repo, init

FIXED_TIME = datetime(2026, 10, 23, 12, 0, tzinfo=timezone(timedelta(hours=2)))


@pytest.fixture(autouse=True)
def isolate_from_parent_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The home folder on this machine is itself a git repo: stop the upward search early.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test User")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    return init(tmp_path / "work")


def write(repo: Repo, rel: str, text: str) -> Path:
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path
