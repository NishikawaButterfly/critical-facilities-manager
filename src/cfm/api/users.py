"""User and API token management endpoints.

Admin-only; the role check is applied once where this router is included.
Token creation is the only response that ever carries a token secret, and it
does so exactly once — the database keeps nothing but the hash.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import UserRole
from ..models import ApiToken, User
from ..schemas import (
    ApiTokenCreate,
    ApiTokenCreatedResponse,
    ApiTokenResponse,
    Page,
    UserCreate,
    UserResponse,
)
from ..services.tokens import create_token, get_token, revoke_token
from ..services.users import create_user, deactivate_user, get_user
from .deps import ActorDep, PageDep, SessionDep
from .serializers import api_token_created_response, api_token_response, user_response

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=201)
def create_user_endpoint(
    payload: UserCreate,
    session: SessionDep,
    actor: ActorDep,
) -> UserResponse:
    user = create_user(
        session,
        actor,
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role,
    )
    return user_response(user)


@router.get("", response_model=Page[UserResponse])
def list_users(
    session: SessionDep,
    page: PageDep,
    role: Annotated[UserRole | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> Page[UserResponse]:
    query = select(User)
    count_query = select(func.count()).select_from(User)
    if role is not None:
        query = query.where(User.role == role.value)
        count_query = count_query.where(User.role == role.value)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)
    total = session.scalar(count_query) or 0
    users = session.scalars(
        query.order_by(User.created_at, User.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[UserResponse](
        items=[user_response(user) for user in users],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user_endpoint(user_id: str, session: SessionDep) -> UserResponse:
    return user_response(get_user(session, user_id))


@router.post("/{user_id}/deactivate", response_model=UserResponse)
def deactivate_user_endpoint(
    user_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> UserResponse:
    user = get_user(session, user_id)
    return user_response(deactivate_user(session, actor, user))


@router.post("/{user_id}/tokens", response_model=ApiTokenCreatedResponse, status_code=201)
def create_token_endpoint(
    user_id: str,
    payload: ApiTokenCreate,
    session: SessionDep,
    actor: ActorDep,
) -> ApiTokenCreatedResponse:
    user = get_user(session, user_id)
    token, secret = create_token(
        session,
        actor,
        user,
        label=payload.label,
        expires_at=payload.expires_at,
    )
    return api_token_created_response(token, secret)


@router.get("/{user_id}/tokens", response_model=Page[ApiTokenResponse])
def list_tokens(user_id: str, session: SessionDep, page: PageDep) -> Page[ApiTokenResponse]:
    user = get_user(session, user_id)
    count_query = select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)
    total = session.scalar(count_query) or 0
    tokens = session.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at, ApiToken.id)
        .offset(page.offset)
        .limit(page.limit)
    ).all()
    return Page[ApiTokenResponse](
        items=[api_token_response(token) for token in tokens],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/{user_id}/tokens/{token_id}/revoke", response_model=ApiTokenResponse)
def revoke_token_endpoint(
    user_id: str,
    token_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> ApiTokenResponse:
    user = get_user(session, user_id)
    token = get_token(session, user, token_id)
    return api_token_response(revoke_token(session, actor, token))
