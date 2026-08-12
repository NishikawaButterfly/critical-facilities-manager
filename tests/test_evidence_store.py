"""The content-addressed evidence store.

Objects are files named by the SHA-256 of their content under sharded
prefix directories, written exactly once (identical content deduplicates),
and re-hashed on every read so corruption is refused instead of served.
Paths are taken from the store's own public surface (``root`` /
``object_path``) because on Windows the store normalizes deep roots to
extended-length form.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from cfm.evidence import EvidenceIntegrityError, EvidenceStore

CONTENT = b"thermal image of the UPS output breaker after the load test"


def store_at(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence")


def stored_files(store: EvidenceStore) -> list[Path]:
    return [path for path in store.root.rglob("*") if path.is_file()]


def test_write_records_the_sha256_of_the_content(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    stored = store.write([CONTENT])
    assert stored.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert stored.size_bytes == len(CONTENT)
    assert stored.deduplicated is False


def test_objects_land_on_sharded_paths(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    stored = store.write([CONTENT])
    path = store.object_path(stored.sha256)
    assert path.is_file()
    assert path.read_bytes() == CONTENT
    assert path.parent.name == stored.sha256[2:4]
    assert path.parent.parent.name == stored.sha256[:2]


def test_read_verified_returns_the_original_bytes(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    stored = store.write([CONTENT[:10], CONTENT[10:]])
    assert store.read_verified(stored.sha256) == CONTENT


def test_identical_content_is_stored_once(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    first = store.write([CONTENT])
    second = store.write([CONTENT])
    assert second.sha256 == first.sha256
    assert second.deduplicated is True
    assert stored_files(store) == [store.object_path(first.sha256)]


def test_read_verified_refuses_a_corrupted_object(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    stored = store.write([CONTENT])
    path = store.object_path(stored.sha256)
    corrupted = bytearray(path.read_bytes())
    corrupted[0] ^= 0xFF
    path.write_bytes(bytes(corrupted))
    with pytest.raises(EvidenceIntegrityError, match=stored.sha256):
        store.read_verified(stored.sha256)


def test_read_verified_refuses_a_missing_object(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    absent = hashlib.sha256(b"never stored").hexdigest()
    with pytest.raises(EvidenceIntegrityError, match=absent):
        store.read_verified(absent)


def test_a_failed_write_leaves_no_partial_files(tmp_path: Path) -> None:
    store = store_at(tmp_path)

    def exploding_chunks() -> Iterator[bytes]:
        yield CONTENT
        raise OSError("connection dropped mid-upload")

    with pytest.raises(OSError, match="mid-upload"):
        store.write(exploding_chunks())
    assert stored_files(store) == []


def test_a_duplicate_write_never_touches_the_existing_object(tmp_path: Path) -> None:
    """Write-once: a stored object's bytes are never rewritten in place."""
    store = store_at(tmp_path)
    stored = store.write([CONTENT])
    path = store.object_path(stored.sha256)
    before = path.stat().st_mtime_ns
    again = store.write([CONTENT])
    assert again.deduplicated is True
    assert path.stat().st_mtime_ns == before
