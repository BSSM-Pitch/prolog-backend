from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.core import errors
from app.core.deps import User
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.auth import service
from app.platform_.auth.google import GoogleOAuth, google_oauth
from app.platform_.auth.schemas import (
    USERNAME_MAX,
    OAuthGoogleRequest,
    OAuthGoogleResponse,
    RefreshRequest,
    SessionResponse,
    SignupRequest,
    TokenResponse,
    UsernameCheckResponse,
    UserResponse,
    UserUpdate,
)

# 인증 '동작' 은 /auth, 사용자 '리소스' 는 /users 아래 둔다 (CLAUDE.md §4.1).
router = APIRouter(prefix="/auth", tags=["AUTH"])
users_router = APIRouter(prefix="/users", tags=["AUTH"])

Google = Annotated[GoogleOAuth, Depends(google_oauth)]


@router.post(
    "/oauth/google",
    summary="Google 로 로그인하거나 가입을 시작한다",
    response_model=OAuthGoogleResponse,
    responses=raises(errors.OAuthProviderError),
)
async def oauth_google(
    body: OAuthGoogleRequest, session: Session, google: Google
) -> dict[str, Any]:
    """Google 이 준 authorization code 를 넘긴다. code 는 1회용이라 이 호출은 한 번만 성공한다.

    - 이미 가입한 사용자: `data` 는 사용자와 토큰, `meta.is_new_user = false`.
    - 처음 온 사용자: 계정을 만들지 않고 `data` 에 `signup_ticket`(10분 유효)과 이메일을 준다.
      `meta.is_new_user = true`. 아이디·역할을 받아 `POST /auth/signup` 으로 가입을 마친다.
    """
    data, is_new_user = await service.oauth_google(session, google, body.oauth_code)
    return ok(data, {"is_new_user": is_new_user})


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    summary="가입을 마친다",
    response_model=Envelope[SessionResponse],
    responses=raises(errors.UsernameRequired, errors.UsernameTaken, errors.SignupTicketInvalid),
)
async def signup(body: SignupRequest, session: Session) -> dict[str, Any]:
    """`POST /auth/oauth/google` 이 준 `signup_ticket` 에 아이디와 역할을 붙여 계정을 만든다.

    성공하면 바로 로그인된 상태(토큰 포함)를 돌려준다. 티켓은 한 번만 쓸 수 있다.
    """
    return ok(await service.signup(session, body))


@router.post(
    "/token/refresh",
    summary="액세스 토큰을 재발급한다",
    response_model=Envelope[TokenResponse],
    responses=raises(errors.RefreshTokenInvalid),
)
async def refresh(body: RefreshRequest, session: Session) -> dict[str, Any]:
    """리프레시 토큰을 새 액세스·리프레시 토큰 쌍으로 바꾼다.

    **로테이션이다.** 넘긴 리프레시 토큰은 즉시 폐기되므로 응답의 새 토큰으로 교체해 저장한다.
    폐기된 토큰을 다시 보내면 `REFRESH_TOKEN_INVALID` 다.
    """
    return ok(await service.refresh(session, body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="로그아웃한다")
async def logout(body: RefreshRequest, session: Session) -> None:
    """리프레시 토큰을 폐기한다. 이미 폐기됐거나 모르는 토큰이어도 204 다.

    액세스 토큰은 만료될 때까지 유효하므로 클라이언트에서 지운다.
    """
    await service.logout(session, body.refresh_token)


@users_router.get(
    "/me",
    summary="내 정보를 조회한다",
    response_model=Envelope[UserResponse],
    responses=raises(errors.UserNotFound),
)
async def me(session: Session, user: User) -> dict[str, Any]:
    """액세스 토큰의 사용자 정보."""
    return ok(await service.me(session, user.id))


@users_router.patch(
    "/me",
    summary="내 역할을 바꾼다",
    response_model=Envelope[UserResponse],
    responses=raises(errors.UserNotFound),
)
async def update_me(body: UserUpdate, session: Session, user: User) -> dict[str, Any]:
    """바꿀 수 있는 것은 `role`(writer · aspiring_writer · reader) 하나다. 아이디는 바꿀 수 없다."""
    return ok(await service.update_me(session, user.id, body.role))


@users_router.get(
    "/check-username",
    summary="아이디 사용 가능 여부를 확인한다",
    response_model=Envelope[UsernameCheckResponse],
)
async def check_username(
    session: Session, username: Annotated[str, Query(min_length=1, max_length=USERNAME_MAX)]
) -> dict[str, Any]:
    """가입 화면용. 로그인하지 않아도 부를 수 있다.

    `available = true` 여도 가입 전에 다른 사람이 먼저 가져갈 수 있다 — 그때 가입은
    `USERNAME_TAKEN` 으로 실패한다.
    """
    return ok(await service.check_username(session, username))
