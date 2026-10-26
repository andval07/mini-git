import pytest

from minigit.errors import MiniGitError
from minigit.index import IndexEntry, read_index, write_index


def _entry(path: str) -> IndexEntry:
    return IndexEntry(path=path, sha="c" * 40, mode=0o100644, size=3, mtime_s=10)


def test_missing_index_is_empty(tmp_path):
    assert read_index(tmp_path) == ()


def test_index_roundtrip(tmp_path):
    entries = (_entry("a.txt"), _entry("src/main.py"))

    write_index(tmp_path, entries)

    assert read_index(tmp_path) == entries


def test_index_entries_are_written_sorted(tmp_path):
    write_index(tmp_path, (_entry("z.txt"), _entry("a.txt")))

    assert [e.path for e in read_index(tmp_path)] == ["a.txt", "z.txt"]


def test_index_file_starts_with_dirc_header(tmp_path):
    write_index(tmp_path, (_entry("a.txt"),))

    data = (tmp_path / "index").read_bytes()

    assert data[:4] == b"DIRC"
    assert int.from_bytes(data[4:8], "big") == 2  # version
    assert int.from_bytes(data[8:12], "big") == 1  # entry count


def test_corrupted_index_raises(tmp_path):
    write_index(tmp_path, (_entry("a.txt"),))
    path = tmp_path / "index"
    data = bytearray(path.read_bytes())
    data[20] ^= 0xFF
    path.write_bytes(bytes(data))

    with pytest.raises(MiniGitError, match="checksum"):
        read_index(tmp_path)


def test_truncated_index_raises_minigit_error(tmp_path):
    import hashlib

    body = b"DIRC" + (2).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x00" * 10
    (tmp_path / "index").write_bytes(body + hashlib.sha1(body).digest())

    with pytest.raises(MiniGitError, match="corrupted"):
        read_index(tmp_path)
