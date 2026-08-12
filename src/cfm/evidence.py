"""Content-addressed, write-once store for evidence files.

An object's path is the SHA-256 of its content under two levels of prefix
directories (``objects/ab/cd/abcd…``), so the store never needs an index
of its own and identical uploads deduplicate by construction. Uploads are
hashed while they stream into a temporary file that is flushed and fsynced
*before* it is atomically renamed onto its final path — callers store the
bytes first and commit the database row referencing the hash afterwards,
so a committed row always points at durable content. A crash between the
two steps leaves at most an orphaned object, which is harmless: it is
invisible without a row, and a retried upload lands on the same path.

Objects are write-once: once a hash exists it is never rewritten (a
concurrent duplicate write is benign because both writers carry, by
construction, identical bytes). Reads recompute the hash and refuse to
return content that no longer matches it — bit rot or tampering surfaces
as an error, never as silently wrong bytes.

This module is the only place that touches the object filesystem, and the
rest of the application depends only on the small :class:`EvidenceStore`
surface (``write`` / ``read_verified``), so an S3-compatible remote
backend can replace the local directory later without touching callers.
That remote backend is documented future work, deliberately not built yet.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

READ_CHUNK_BYTES = 1024 * 1024


class EvidenceIntegrityError(Exception):
    """A stored object is missing or its bytes no longer match its hash."""


@dataclass(frozen=True)
class StoredObject:
    """What one completed write established about the stored content."""

    sha256: str
    size_bytes: int
    deduplicated: bool
    """True when the content already existed and no new object was written."""


class EvidenceStore:
    """A content-addressed object store rooted at one local directory."""

    def __init__(self, root: Path) -> None:
        self._root = _storage_root(root)
        self._objects = self._root / "objects"
        self._incoming = self._root / "incoming"

    @property
    def root(self) -> Path:
        """The normalized store root (extended-length form on Windows)."""
        return self._root

    def object_path(self, sha256_hex: str) -> Path:
        """Where the object for this hash lives (or would live) on disk.

        Exposed for operators and tests; the API never hands out paths.
        """
        return self._objects / sha256_hex[:2] / sha256_hex[2:4] / sha256_hex

    def write(self, chunks: Iterable[bytes]) -> StoredObject:
        """Hash ``chunks`` while streaming them into the store.

        The temporary file is fsynced before the atomic rename, so once
        this returns the content is durable under its hash. If the final
        path already exists the temporary file is discarded unread —
        write-once semantics, and deduplication for free. Any error while
        consuming ``chunks`` (including a caller aborting an oversized
        upload mid-stream) removes the partial file and re-raises.
        """
        self._incoming.mkdir(parents=True, exist_ok=True)
        temp_path = self._incoming / f"{uuid4()}.part"
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with temp_path.open("wb") as handle:
                for chunk in chunks:
                    digest.update(chunk)
                    size_bytes += len(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            sha256_hex = digest.hexdigest()
            final_path = self.object_path(sha256_hex)
            if final_path.exists():
                temp_path.unlink()
                return StoredObject(sha256_hex, size_bytes, deduplicated=True)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.replace(final_path)
            _fsync_directory(final_path.parent)
            return StoredObject(sha256_hex, size_bytes, deduplicated=False)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    def read_verified(self, sha256_hex: str) -> bytes:
        """The object's bytes, re-hashed and compared before they leave.

        Verification happens on the whole content *before* anything is
        returned, rather than while streaming to the client: a stream can
        only detect a mismatch after the corrupted bytes have already been
        sent with a success status. The buffering this costs is bounded by
        the upload size cap, because only capped content ever enters the
        store. A missing or mismatching object raises
        :class:`EvidenceIntegrityError`; it is the caller's job to turn
        that into a clear server error instead of serving wrong bytes.
        """
        path = self.object_path(sha256_hex)
        if not path.is_file():
            raise EvidenceIntegrityError(f"Evidence object {sha256_hex} is missing from the store.")
        digest = hashlib.sha256()
        content = bytearray()
        with path.open("rb") as handle:
            while chunk := handle.read(READ_CHUNK_BYTES):
                digest.update(chunk)
                content.extend(chunk)
        actual = digest.hexdigest()
        if actual != sha256_hex:
            raise EvidenceIntegrityError(
                f"Evidence object {sha256_hex} failed verification: its content "
                f"hashes to {actual}. The object is corrupted and will not be served."
            )
        return bytes(content)


def _storage_root(root: Path) -> Path:
    """The store root as an absolute path — extended-length on Windows.

    An object path appends two shard directories and a 64-character hash to
    the root, which pushes a deep root past the legacy 260-character Windows
    path limit; the ``\\\\?\\`` prefix opts file operations out of that
    limit. Drive-letter paths are prefixed; UNC and already-extended paths
    pass through untouched. On other platforms the root is only made
    absolute.
    """
    absolute = root.resolve()
    if os.name == "nt" and not str(absolute).startswith("\\\\"):
        return Path(f"\\\\?\\{absolute}")
    return absolute


def _fsync_directory(directory: Path) -> None:
    """Flush the directory entry so the rename itself is durable.

    POSIX only: Windows cannot open a directory for fsync, so there the
    rename's durability rides on the filesystem's own metadata journaling
    (NTFS); the object's bytes are fsynced on every platform above.
    """
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
