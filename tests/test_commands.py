import pytest

from conftest import FIXED_TIME, write
from minigit import commands
from minigit.errors import MiniGitError
from minigit.index import read_index
from minigit.objects import read_object
from minigit.repo import current_branch, find_repo, head_commit, init, resolve_revision


def _commit_file(repo, rel, text, message):
    write(repo, rel, text)
    commands.add(repo, [repo.root / rel])
    return commands.commit(repo, message, when=FIXED_TIME)


def test_init_creates_git_layout(repo):
    assert (repo.gitdir / "objects").is_dir()
    assert (repo.gitdir / "refs" / "heads").is_dir()
    assert (repo.gitdir / "HEAD").read_bytes() == b"ref: refs/heads/main\n"


def test_init_twice_fails(repo):
    with pytest.raises(MiniGitError, match="already exists"):
        init(repo.root)


def test_find_repo_from_subdirectory(repo):
    sub = repo.root / "a" / "b"
    sub.mkdir(parents=True)

    assert find_repo(sub) == repo


def test_find_repo_outside_repository_fails(tmp_path):
    with pytest.raises(MiniGitError, match="not a git repository"):
        find_repo(tmp_path)


def test_add_stages_file_content_as_blob(repo):
    write(repo, "hello.txt", "hello\n")

    commands.add(repo, [repo.root / "hello.txt"])

    (entry,) = read_index(repo.gitdir)
    assert entry.path == "hello.txt"
    assert entry.sha == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert read_object(repo.gitdir, entry.sha) == ("blob", b"hello\n")


def test_add_unknown_path_fails(repo):
    with pytest.raises(MiniGitError, match="did not match"):
        commands.add(repo, [repo.root / "nope.txt"])


def test_commit_records_tree_and_moves_branch(repo):
    sha = _commit_file(repo, "src/app.py", "print('hi')\n", "first commit")

    commit = commands.read_commit(repo.gitdir, sha)
    assert head_commit(repo) == sha
    assert commit.parents == ()
    assert commit.message == "first commit\n"
    assert commit.author == "Test User <test@example.com> 1792749600 +0200"
    assert commands.flatten_tree(repo.gitdir, commit.tree).keys() == {"src/app.py"}


def test_second_commit_has_parent_and_log_is_newest_first(repo):
    first = _commit_file(repo, "a.txt", "1\n", "one")
    second = _commit_file(repo, "a.txt", "2\n", "two")

    history = commands.log(repo)

    assert [sha for sha, _ in history] == [second, first]
    assert history[0][1].parents == (first,)


def test_commit_without_changes_fails(repo):
    _commit_file(repo, "a.txt", "1\n", "one")

    with pytest.raises(MiniGitError, match="nothing to commit"):
        commands.commit(repo, "again", when=FIXED_TIME)


def test_commit_with_empty_index_fails(repo):
    with pytest.raises(MiniGitError, match="nothing to commit"):
        commands.commit(repo, "empty", when=FIXED_TIME)


def test_commit_with_empty_message_fails(repo):
    write(repo, "a.txt", "1\n")
    commands.add(repo, [repo.root / "a.txt"])

    with pytest.raises(MiniGitError, match="message"):
        commands.commit(repo, "   ", when=FIXED_TIME)


def test_status_reports_staged_unstaged_and_untracked(repo):
    _commit_file(repo, "kept.txt", "same\n", "base")
    _commit_file(repo, "edited.txt", "v1\n", "base 2")
    write(repo, "new.txt", "new\n")
    commands.add(repo, [repo.root / "new.txt"])
    write(repo, "edited.txt", "v2\n")
    write(repo, "loose.txt", "?\n")

    status = commands.status(repo)

    assert status.branch == "main"
    assert status.staged == (("new file", "new.txt"),)
    assert status.unstaged == (("modified", "edited.txt"),)
    assert status.untracked == ("loose.txt",)
    assert not status.is_clean


def test_status_is_clean_right_after_commit(repo):
    _commit_file(repo, "a.txt", "1\n", "one")

    assert commands.status(repo).is_clean


def test_add_directory_stages_removed_files_as_deleted(repo):
    write(repo, "docs/a.md", "a\n")
    _commit_file(repo, "docs/b.md", "b\n", "docs")
    (repo.root / "docs" / "b.md").unlink()

    commands.add(repo, [repo.root / "docs"])

    assert commands.status(repo).staged == (("new file", "docs/a.md"), ("deleted", "docs/b.md"))


def test_checkout_branch_restores_files_and_moves_head(repo):
    _commit_file(repo, "a.txt", "main version\n", "on main")
    commands.branch(repo, "feature")
    commands.checkout(repo, "feature")
    _commit_file(repo, "extra/b.txt", "only on feature\n", "on feature")

    commands.checkout(repo, "main")

    assert current_branch(repo) == "main"
    assert (repo.root / "a.txt").read_bytes() == b"main version\n"
    assert not (repo.root / "extra").exists()
    assert commands.status(repo).is_clean


def test_checkout_refuses_with_uncommitted_changes(repo):
    _commit_file(repo, "a.txt", "1\n", "one")
    commands.branch(repo, "other")
    write(repo, "a.txt", "dirty\n")

    with pytest.raises(MiniGitError, match="uncommitted"):
        commands.checkout(repo, "other")


def test_checkout_refuses_to_overwrite_untracked_file(repo):
    _commit_file(repo, "a.txt", "1\n", "one")
    commands.branch(repo, "feature")
    commands.checkout(repo, "feature")
    _commit_file(repo, "b.txt", "tracked on feature\n", "add b")
    commands.checkout(repo, "main")
    write(repo, "b.txt", "my untracked work\n")

    with pytest.raises(MiniGitError, match="untracked"):
        commands.checkout(repo, "feature")
    assert (repo.root / "b.txt").read_bytes() == b"my untracked work\n"


def test_checkout_commit_detaches_head(repo):
    first = _commit_file(repo, "a.txt", "1\n", "one")
    _commit_file(repo, "a.txt", "2\n", "two")

    commands.checkout(repo, first[:7])

    assert current_branch(repo) is None
    assert head_commit(repo) == first
    assert (repo.root / "a.txt").read_bytes() == b"1\n"


def test_resolve_revision_accepts_head_branch_and_short_sha(repo):
    sha = _commit_file(repo, "a.txt", "1\n", "one")

    assert resolve_revision(repo, "HEAD") == sha
    assert resolve_revision(repo, "main") == sha
    assert resolve_revision(repo, sha[:6]) == sha


def test_resolve_unknown_revision_fails(repo):
    with pytest.raises(MiniGitError, match="unknown revision"):
        resolve_revision(repo, "nothing")


def test_branch_rejects_invalid_or_duplicate_name(repo):
    _commit_file(repo, "a.txt", "1\n", "one")

    with pytest.raises(MiniGitError, match="invalid branch name"):
        commands.branch(repo, "bad name")
    with pytest.raises(MiniGitError, match="already exists"):
        commands.branch(repo, "main")


def test_branch_before_first_commit_fails(repo):
    with pytest.raises(MiniGitError, match="no commits"):
        commands.branch(repo, "feature")


def test_cat_file_pretty_prints_tree(repo):
    sha = _commit_file(repo, "a.txt", "1\n", "one")
    tree = commands.read_commit(repo.gitdir, sha).tree

    output = commands.cat_file(repo, tree)

    assert output == f"100644 blob {commands.flatten_tree(repo.gitdir, tree)['a.txt'].sha}\ta.txt"


def test_add_refuses_files_inside_dot_git(repo):
    with pytest.raises(MiniGitError, match=".git"):
        commands.add(repo, [repo.gitdir / "config"])


def test_revision_cannot_escape_refs_folder(repo):
    _commit_file(repo, "a.txt", "1\n", "one")

    with pytest.raises(MiniGitError, match="unknown revision"):
        resolve_revision(repo, "../../HEAD")
    with pytest.raises(MiniGitError, match="unknown revision"):
        commands.checkout(repo, "../../HEAD")
