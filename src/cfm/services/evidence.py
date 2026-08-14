"""Evidence services: store uploads verified, attach them, serve them verified.

The attach is the auditable act. Every successful upload writes the bytes
to the content-addressed store first — fsynced before anything commits —
and then, in one transaction, the object row (created on first sight of
the content, reused on deduplication), the attachment row binding it to
its target, and an audit entry on the target carrying the SHA-256 and the
declaration made in that request. Downloads re-hash the stored bytes and
refuse to serve content that no longer matches. Nothing here updates or
deletes: evidence is append-only, like the audit trail it feeds.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import BinaryIO

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import (
    COMMISSIONING_TRANSITIONS,
    ORDER_TRANSITIONS,
    PERMIT_TRANSITIONS,
    PUNCH_ITEM_TRANSITIONS,
    CommissioningTestStatus,
    MaintenanceOrderStatus,
    PunchItemStatus,
    WorkPermitStatus,
)
from ..errors import (
    ConflictError,
    EvidenceCorruptedError,
    NotFoundError,
    PayloadTooLargeError,
    RuleViolationError,
)
from ..evidence import EvidenceIntegrityError, EvidenceStore
from ..models import (
    CommissioningTest,
    EvidenceAttachment,
    EvidenceObject,
    MaintenanceOrder,
    PunchItem,
    WorkPermit,
)
from .commissioning import ENTITY_TYPE as COMMISSIONING_ENTITY_TYPE
from .maintenance import ENTITY_TYPE as ORDER_ENTITY_TYPE
from .permits import ENTITY_TYPE as PERMIT_ENTITY_TYPE
from .punch_items import ENTITY_TYPE as PUNCH_ITEM_ENTITY_TYPE

AttachTarget = CommissioningTest | MaintenanceOrder | PunchItem | WorkPermit

UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_CONTENT_TYPE = "application/octet-stream"
FILENAME_MAX_LENGTH = 255


def _attach_point(target: AttachTarget) -> tuple[str, str, bool]:
    """(audit entity type, attachment column, target is in a terminal state).

    Terminal is read from the domain transition tables, so the rule stays
    "evidence attaches while the record can still change" for every target
    — the same rule text evidence notes already follow on commissioning
    tests, where pending is the only non-terminal state.
    """
    if isinstance(target, CommissioningTest):
        terminal = not COMMISSIONING_TRANSITIONS[CommissioningTestStatus(target.status)]
        return COMMISSIONING_ENTITY_TYPE, "commissioning_test_id", terminal
    if isinstance(target, PunchItem):
        terminal = not PUNCH_ITEM_TRANSITIONS[PunchItemStatus(target.status)]
        return PUNCH_ITEM_ENTITY_TYPE, "punch_item_id", terminal
    if isinstance(target, MaintenanceOrder):
        terminal = not ORDER_TRANSITIONS[MaintenanceOrderStatus(target.status)]
        return ORDER_ENTITY_TYPE, "order_id", terminal
    terminal = not PERMIT_TRANSITIONS[WorkPermitStatus(target.status)]
    return PERMIT_ENTITY_TYPE, "permit_id", terminal


def safe_filename(declared: str | None) -> str:
    """The declared filename reduced to one safe path-less component.

    Directory parts are stripped, control characters and double quotes are
    removed (the name is echoed inside a Content-Disposition header), and
    the result is bounded. An upload without a usable name is rejected:
    the declared filename is part of what the audit trail records about
    the attach, so it cannot be empty.
    """
    candidate = (declared or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        character for character in candidate if character.isprintable() and character != '"'
    )
    cleaned = cleaned.strip().strip(".")[:FILENAME_MAX_LENGTH].strip()
    if not cleaned:
        raise RuleViolationError(
            "An evidence upload needs a usable declared filename.",
            error_code="evidence.filename_required",
        )
    return cleaned


def _capped_chunks(
    first: bytes, stream: BinaryIO, max_bytes: int, filename: str
) -> Iterator[bytes]:
    total = 0
    chunk = first
    while chunk:
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"Evidence file {filename!r} exceeds the upload limit of {max_bytes} bytes."
            )
        yield chunk
        chunk = stream.read(UPLOAD_CHUNK_BYTES)


def read_evidence_object(
    session: Session, object_id: str, readable: ColumnElement[bool]
) -> EvidenceObject:
    """The stored object addressed by ``object_id``, if this request may read it.

    An evidence object is readable through the domain objects it evidences:
    the same bytes may hang off several targets, and one attachment the
    caller's grants cover is enough. An object with no such attachment
    answers exactly what a missing one answers, so evidence cannot become a
    way around the scope of the record it belongs to.
    """
    evidence_object = session.scalar(
        select(EvidenceObject).where(EvidenceObject.id == object_id, readable)
    )
    if evidence_object is None:
        raise NotFoundError(
            f"Evidence object {object_id} was not found.",
            error_code="evidence.not_found",
        )
    return evidence_object


def attachments_for(session: Session, target: AttachTarget) -> Sequence[EvidenceAttachment]:
    _, column, _ = _attach_point(target)
    return session.scalars(
        select(EvidenceAttachment)
        .where(getattr(EvidenceAttachment, column) == target.id)
        .order_by(EvidenceAttachment.id)
    ).all()


def store_and_attach(
    session: Session,
    store: EvidenceStore,
    actor: str,
    *,
    target: AttachTarget,
    stream: BinaryIO,
    declared_filename: str | None,
    declared_content_type: str | None,
    note: str | None,
    max_bytes: int,
) -> EvidenceAttachment:
    """Store one uploaded file and attach it to ``target``, audited.

    The content is hashed while it streams into the store, capped at
    ``max_bytes``, and durable (fsynced) before the transaction below
    commits — a committed attachment never references bytes that could
    still be lost. A crash after the store write leaves an orphaned
    object, which is harmless: content addressing makes the retry land on
    the same path. The declared filename and content type are client
    declarations, recorded as such; the hash and size are measured
    server-side and are what an audit can rely on.
    """
    entity_type, column, terminal = _attach_point(target)
    if terminal:
        raise ConflictError(
            f"Evidence can only be attached while the {entity_type.replace('_', ' ')} "
            f"is active; this one is {target.status!r}.",
            error_code="evidence.target_not_active",
        )
    filename = safe_filename(declared_filename)
    content_type = (declared_content_type or "").strip()[:255] or DEFAULT_CONTENT_TYPE

    first = stream.read(UPLOAD_CHUNK_BYTES)
    if not first:
        raise RuleViolationError(
            "An evidence file needs content; the upload was empty.",
            error_code="evidence.empty",
        )
    stored = store.write(_capped_chunks(first, stream, max_bytes, filename))

    evidence_object = session.scalar(
        select(EvidenceObject).where(EvidenceObject.sha256 == stored.sha256)
    )
    if evidence_object is None:
        evidence_object = EvidenceObject(
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            filename=filename,
            content_type=content_type,
            uploaded_by=actor,
        )
        session.add(evidence_object)
        session.flush()

    already_attached = session.scalar(
        select(EvidenceAttachment).where(
            EvidenceAttachment.evidence_object_id == evidence_object.id,
            getattr(EvidenceAttachment, column) == target.id,
        )
    )
    if already_attached is not None:
        raise ConflictError(
            f"Evidence object {evidence_object.id} (sha256 {stored.sha256}) is already "
            f"attached to this {entity_type.replace('_', ' ')}.",
            error_code="evidence.already_attached",
        )

    cleaned_note = note.strip() if note is not None else None
    attachment = EvidenceAttachment(
        evidence_object_id=evidence_object.id,
        note=cleaned_note or None,
        attached_by=actor,
    )
    setattr(attachment, column, target.id)
    session.add(attachment)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=entity_type,
        entity_id=target.id,
        action="evidence_attached",
        before=None,
        after={
            "attachment_id": attachment.id,
            "evidence_object_id": evidence_object.id,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "filename": filename,
            "content_type": content_type,
            "note": cleaned_note or None,
            "content_already_stored": stored.deduplicated,
        },
    )
    session.commit()
    return attachment


def verified_content(store: EvidenceStore, evidence_object: EvidenceObject) -> bytes:
    """The stored bytes, re-hashed against the recorded SHA-256 first.

    A mismatch — bit rot, tampering, a missing file — surfaces as a clear
    500 instead of silently wrong evidence with a success status.
    """
    try:
        return store.read_verified(evidence_object.sha256)
    except EvidenceIntegrityError as exc:
        raise EvidenceCorruptedError(
            f"Evidence object {evidence_object.id} cannot be served: {exc}"
        ) from exc
