import pytest

from minigit.cli import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test User")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_full_flow_through_cli(workdir, capsys):
    assert main(["init"]) == 0
    (workdir / "a.txt").write_bytes(b"hello\n")
    assert main(["add", "a.txt"]) == 0
    assert main(["commit", "-m", "first commit"]) == 0
    capsys.readouterr()

    assert main(["log"]) == 0
    out = capsys.readouterr().out
    assert "first commit" in out
    assert "Author: Test User <test@example.com>" in out

    assert main(["status"]) == 0
    assert "nothing to commit, working tree clean" in capsys.readouterr().out


def test_hash_object_prints_git_compatible_sha(workdir, capsys):
    (workdir / "h.txt").write_bytes(b"hello\n")

    assert main(["hash-object", "h.txt"]) == 0

    assert capsys.readouterr().out.strip() == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_errors_print_fatal_and_return_1(workdir, capsys):
    assert main(["status"]) == 1

    assert capsys.readouterr().err.startswith("fatal: not a git repository")
