from typing import Any

from fastapi import APIRouter, status

from app.core.deps import User
from app.core.response import ok
from app.db.session import Session
from app.platform_.auth import service
from app.platform_.auth.schemas import LoginRequest, RefreshRequest, SignupRequest

router = APIRouter(prefix="/auth", tags=["AUTH"])


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: Session) -> dict[str, Any]:
    return ok(await service.signup(session, body))


@router.post("/login")
async def login(body: LoginRequest, session: Session) -> dict[str, Any]:
    return ok(await service.login(session, body))


@router.post("/refresh")
async def refresh(body: RefreshRequest, session: Session) -> dict[str, Any]:
    return ok(await service.refresh(session, body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: Session) -> None:
    await service.logout(session, body.refresh_token)


@router.get("/me")
async def me(session: Session, user: User) -> dict[str, Any]:
    return ok(await service.me(session, user.id))
