import pytest

from minigit.errors import MiniGitError
from minigit.objects import (
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


def test_blob_hash_matches_git_for_known_content():
    # Same value as `echo hello | git hash-object --stdin`
    assert hash_object("blob", b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_empty_blob_and_empty_tree_hashes_match_git():
    assert hash_object("blob", b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert hash_object("tree", b"") == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_unknown_object_type_is_rejected():
    with pytest.raises(MiniGitError):
        hash_object("banana", b"x")


def test_write_then_read_object_roundtrip(tmp_path):
    sha = write_object(tmp_path, "blob", b"some content")

    assert (tmp_path / "objects" / sha[:2] / sha[2:]).is_file()
    assert read_object(tmp_path, sha) == ("blob", b"some content")


def test_read_missing_object_raises(tmp_path):
    with pytest.raises(MiniGitError, match="not found"):
        read_object(tmp_path, "0" * 40)


def test_tree_sorts_directories_as_if_they_end_with_slash():
    folder = TreeEntry(MODE_TREE, "a", "2" * 40)
    file = TreeEntry(MODE_FILE, "a.txt", "1" * 40)

    entries = parse_tree(serialize_tree([folder, file]))

    # "a.txt" < "a/" because "." (0x2e) comes before "/" (0x2f)
    assert [e.name for e in entries] == ["a.txt", "a"]
    assert entries[1].is_tree


def test_commit_roundtrip():
    commit = Commit(
        tree="a" * 40,
        parents=("b" * 40,),
        author="Test User <test@example.com> 1792749600 +0200",
        committer="Test User <test@example.com> 1792749600 +0200",
        message="first line\n\nbody\n",
    )

    assert parse_commit(serialize_commit(commit)) == commit


def test_parse_commit_skips_multiline_signature_header():
    data = (
        b"tree " + b"a" * 40 + b"\n"
        b"author A <a@x> 1 +0000\n"
        b"committer A <a@x> 1 +0000\n"
        b"gpgsig -----BEGIN PGP SIGNATURE-----\n"
        b" abc\n"
        b" -----END PGP SIGNATURE-----\n"
        b"\n"
        b"signed\n"
    )

    commit = parse_commit(data)

    assert commit.parents == ()
    assert commit.message == "signed\n"


def test_tree_with_unsafe_names_is_rejected():
    for bad in ("..", ".", ".git", ".GIT", "a/b", "a\\b", ""):
        data = f"100644 {bad}".encode() + b"\0" + b"\x01" * 20
        with pytest.raises(MiniGitError, match="unsafe"):
            parse_tree(data)


def test_truncated_tree_raises_minigit_error():
    with pytest.raises(MiniGitError):
        parse_tree(b"100644 name-without-terminator")
