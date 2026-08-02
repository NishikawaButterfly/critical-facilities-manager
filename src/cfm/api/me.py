"""The authenticated caller's own identity."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import UserResponse
from .deps import CurrentUserDep
from .serializers import user_response

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserResponse)
def read_me(user: CurrentUserDep) -> UserResponse:
    return user_response(user)
