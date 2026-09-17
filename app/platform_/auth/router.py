from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import User
from app.core.response import ok
from app.db.session import Session
from app.platform_.auth import service
from app.platform_.auth.google import GoogleOAuth, google_oauth
from app.platform_.auth.schemas import (
    USERNAME_MAX,
    OAuthGoogleRequest,
    RefreshRequest,
    SignupRequest,
    UserUpdate,
)

# 인증 '동작' 은 /auth, 사용자 '리소스' 는 /users 아래 둔다 (CLAUDE.md §4.1).
router = APIRouter(prefix="/auth", tags=["AUTH"])
users_router = APIRouter(prefix="/users", tags=["AUTH"])

Google = Annotated[GoogleOAuth, Depends(google_oauth)]


@router.post("/oauth/google")
async def oauth_google(
    body: OAuthGoogleRequest, session: Session, google: Google
) -> dict[str, Any]:
    data, is_new_user = await service.oauth_google(session, google, body.oauth_code)
    return ok(data, {"is_new_user": is_new_user})


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: Session) -> dict[str, Any]:
    return ok(await service.signup(session, body))


@router.post("/token/refresh")
async def refresh(body: RefreshRequest, session: Session) -> dict[str, Any]:
    return ok(await service.refresh(session, body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: Session) -> None:
    await service.logout(session, body.refresh_token)


@users_router.get("/me")
async def me(session: Session, user: User) -> dict[str, Any]:
    return ok(await service.me(session, user.id))


@users_router.patch("/me")
async def update_me(body: UserUpdate, session: Session, user: User) -> dict[str, Any]:
    return ok(await service.update_me(session, user.id, body.role))


@users_router.get("/check-username")
async def check_username(
    session: Session, username: Annotated[str, Query(min_length=1, max_length=USERNAME_MAX)]
) -> dict[str, Any]:
    return ok(await service.check_username(session, username))
