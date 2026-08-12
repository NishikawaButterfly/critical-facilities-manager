"""Evidence file endpoints: upload-and-attach, list, metadata, download.

Uploading is always an attach: the file arrives as multipart form data on
the domain object it evidences, the site-scope check guards it exactly
like any other write on that target, and the audit entry carries the
content hash. Downloads are reads and stay installation-wide, consistent
with every other read in this API; the bytes are re-verified against the
recorded hash before they are served, always with an attachment
disposition and ``nosniff`` because the content type is a client
declaration. There is no delete endpoint anywhere below: evidence is
append-only, like the audit trail it feeds.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from ..authz import site_of_asset_id, site_of_order_id, site_of_punch_item
from ..config import Settings
from ..evidence import EvidenceStore
from ..schemas import EvidenceAttachmentResponse, EvidenceObjectResponse
from ..services.commissioning import get_test
from ..services.evidence import (
    AttachTarget,
    attachments_for,
    get_evidence_object,
    store_and_attach,
    verified_content,
)
from ..services.maintenance import get_order
from ..services.permits import get_permit
from ..services.punch_items import get_punch_item
from .deps import (
    ActorDep,
    EvidenceStoreDep,
    SessionDep,
    SettingsDep,
    SiteAccessDep,
)
from .serializers import evidence_attachment_response, evidence_object_response

router = APIRouter(tags=["evidence"])

UploadDep = Annotated[UploadFile, File(description="The evidence file to store and attach.")]
NoteDep = Annotated[
    str | None,
    Form(description="Optional note recording why this file evidences the target."),
]


def _attach(
    session: Session,
    *,
    actor: str,
    store: EvidenceStore,
    settings: Settings,
    target: AttachTarget,
    file: UploadFile,
    note: str | None,
) -> EvidenceAttachmentResponse:
    attachment = store_and_attach(
        session,
        store,
        actor,
        target=target,
        stream=file.file,
        declared_filename=file.filename,
        declared_content_type=file.content_type,
        note=note,
        max_bytes=settings.evidence_max_mb * 1024 * 1024,
    )
    return evidence_attachment_response(attachment)


@router.post(
    "/commissioning-tests/{test_id}/evidence-files",
    response_model=EvidenceAttachmentResponse,
    status_code=201,
)
def attach_to_commissioning_test(
    test_id: str,
    *,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    store: EvidenceStoreDep,
    settings: SettingsDep,
    file: UploadDep,
    note: NoteDep = None,
) -> EvidenceAttachmentResponse:
    test = get_test(session, test_id)
    access.require_site(site_of_asset_id(session, test.asset_id))
    return _attach(
        session, actor=actor, store=store, settings=settings, target=test, file=file, note=note
    )


@router.get(
    "/commissioning-tests/{test_id}/evidence-files",
    response_model=list[EvidenceAttachmentResponse],
)
def list_commissioning_test_evidence(
    test_id: str, session: SessionDep
) -> list[EvidenceAttachmentResponse]:
    test = get_test(session, test_id)
    return [evidence_attachment_response(item) for item in attachments_for(session, test)]


@router.post(
    "/punch-items/{punch_item_id}/evidence-files",
    response_model=EvidenceAttachmentResponse,
    status_code=201,
)
def attach_to_punch_item(
    punch_item_id: str,
    *,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    store: EvidenceStoreDep,
    settings: SettingsDep,
    file: UploadDep,
    note: NoteDep = None,
) -> EvidenceAttachmentResponse:
    item = get_punch_item(session, punch_item_id)
    access.require_site(site_of_punch_item(session, item))
    return _attach(
        session, actor=actor, store=store, settings=settings, target=item, file=file, note=note
    )


@router.get(
    "/punch-items/{punch_item_id}/evidence-files",
    response_model=list[EvidenceAttachmentResponse],
)
def list_punch_item_evidence(
    punch_item_id: str, session: SessionDep
) -> list[EvidenceAttachmentResponse]:
    item = get_punch_item(session, punch_item_id)
    return [evidence_attachment_response(entry) for entry in attachments_for(session, item)]


@router.post(
    "/maintenance-orders/{order_id}/evidence-files",
    response_model=EvidenceAttachmentResponse,
    status_code=201,
)
def attach_to_order(
    order_id: str,
    *,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    store: EvidenceStoreDep,
    settings: SettingsDep,
    file: UploadDep,
    note: NoteDep = None,
) -> EvidenceAttachmentResponse:
    order = get_order(session, order_id)
    access.require_site(site_of_asset_id(session, order.asset_id))
    return _attach(
        session, actor=actor, store=store, settings=settings, target=order, file=file, note=note
    )


@router.get(
    "/maintenance-orders/{order_id}/evidence-files",
    response_model=list[EvidenceAttachmentResponse],
)
def list_order_evidence(order_id: str, session: SessionDep) -> list[EvidenceAttachmentResponse]:
    order = get_order(session, order_id)
    return [evidence_attachment_response(entry) for entry in attachments_for(session, order)]


@router.post(
    "/work-permits/{permit_id}/evidence-files",
    response_model=EvidenceAttachmentResponse,
    status_code=201,
)
def attach_to_permit(
    permit_id: str,
    *,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    store: EvidenceStoreDep,
    settings: SettingsDep,
    file: UploadDep,
    note: NoteDep = None,
) -> EvidenceAttachmentResponse:
    permit = get_permit(session, permit_id)
    access.require_site(site_of_order_id(session, permit.order_id))
    return _attach(
        session, actor=actor, store=store, settings=settings, target=permit, file=file, note=note
    )


@router.get(
    "/work-permits/{permit_id}/evidence-files",
    response_model=list[EvidenceAttachmentResponse],
)
def list_permit_evidence(permit_id: str, session: SessionDep) -> list[EvidenceAttachmentResponse]:
    permit = get_permit(session, permit_id)
    return [evidence_attachment_response(entry) for entry in attachments_for(session, permit)]


@router.get("/evidence-objects/{object_id}", response_model=EvidenceObjectResponse)
def get_evidence_object_endpoint(object_id: str, session: SessionDep) -> EvidenceObjectResponse:
    return evidence_object_response(get_evidence_object(session, object_id))


def _content_disposition(filename: str) -> str:
    """An attachment disposition safe for the latin-1 header encoding.

    The plain ``filename`` carries an ASCII-only fallback; the RFC 5987
    ``filename*`` parameter carries the declared name percent-encoded, so
    non-ASCII filenames survive without breaking header encoding.
    """
    fallback = "".join(
        character if character.isascii() and character not in '";\\' else "_"
        for character in filename
    )
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback or 'evidence'}\"; filename*=UTF-8''{encoded}"


@router.get("/evidence-objects/{object_id}/content", response_class=Response)
def download_evidence_object(
    object_id: str, session: SessionDep, store: EvidenceStoreDep
) -> Response:
    """The stored bytes, re-verified against the recorded SHA-256 first.

    A corrupted or missing object answers 500 instead of serving silently
    wrong bytes. Content is always served as an attachment with
    ``nosniff`` because the content type is a client declaration.
    """
    evidence_object = get_evidence_object(session, object_id)
    content = verified_content(store, evidence_object)
    return Response(
        content=content,
        media_type=evidence_object.content_type,
        headers={
            "Content-Disposition": _content_disposition(evidence_object.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )
