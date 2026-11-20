from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.core import errors
from app.core.deps import User
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.auth import service
from app.platform_.auth.google import GoogleOAuth, google_oauth
from app.platform_.auth.naver import NaverOAuth, naver_oauth
from app.platform_.auth.schemas import (
    USERNAME_MAX,
    LoginRequest,
    OAuthGoogleRequest,
    OAuthGoogleResponse,
    OAuthNaverRequest,
    OAuthNaverResponse,
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
Naver = Annotated[NaverOAuth, Depends(naver_oauth)]


@router.post(
    "/oauth/google",
    summary="Google 로 로그인하거나 가입을 시작한다",
    response_model=OAuthGoogleResponse,
    responses=raises(errors.OAuthProviderError, errors.AccountBanned),
)
async def oauth_google(
    body: OAuthGoogleRequest, session: Session, google: Google
) -> dict[str, Any]:
    """Google 이 준 authorization code 를 넘긴다. code 는 1회용이라 이 호출은 한 번만 성공한다.

    - 이미 가입한 사용자: `data` 는 사용자와 토큰, `meta.is_new_user = false`.
    - 처음 온 사용자: 계정을 만들지 않고 `data` 에 `signup_ticket`(10분 유효)과 이메일을 준다.
      `meta.is_new_user = true`. 닉네임·역할을 받아 `POST /auth/signup` 으로 가입을 마친다.
    """
    identity = await google.exchange(body.oauth_code)
    data, is_new_user = await service.oauth_login(session, service.PROVIDER, identity)
    return ok(data, {"is_new_user": is_new_user})


@router.post(
    "/oauth/naver",
    summary="네이버로 로그인하거나 가입을 시작한다",
    response_model=OAuthNaverResponse,
    responses=raises(errors.OAuthProviderError, errors.AccountBanned),
)
async def oauth_naver(body: OAuthNaverRequest, session: Session, naver: Naver) -> dict[str, Any]:
    """네이버가 준 authorization code 와 인가 요청 때 보낸 `state` 를 넘긴다.

    응답은 `POST /auth/oauth/google` 과 같다 — 신규면 `signup_ticket` 을 받아
    `POST /auth/signup` 으로 가입을 마친다.
    """
    identity = await naver.exchange(body.oauth_code, body.state)
    data, is_new_user = await service.oauth_login(session, service.NAVER, identity)
    return ok(data, {"is_new_user": is_new_user})


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    summary="가입을 마친다",
    response_model=Envelope[SessionResponse],
    responses=raises(
        errors.InvalidInput,
        errors.UsernameRequired,
        errors.EmailTaken,
        errors.SignupTicketInvalid,
    ),
)
async def signup(body: SignupRequest, session: Session) -> dict[str, Any]:
    """두 가지 가입을 받는다.

    - **소셜:** `POST /auth/oauth/google` 이 준 `signup_ticket` + 닉네임·역할.
      티켓은 한 번만 쓸 수 있다.
    - **일반:** `email` + `password`(영문·숫자 포함 8자 이상) + 닉네임·역할.

    성공하면 바로 로그인된 상태(토큰 포함)를 돌려준다.
    """
    return ok(await service.signup(session, body))


@router.post(
    "/login",
    summary="이메일·비밀번호로 로그인한다",
    response_model=Envelope[SessionResponse],
    responses=raises(errors.InvalidCredentials, errors.AccountBanned),
)
async def login(body: LoginRequest, session: Session) -> dict[str, Any]:
    """`email` 로 로그인한다. 기존 `login_id` 필드도 이메일 별칭으로 받는다.
    소셜 계정은 이 경로로 로그인할 수 없다(`INVALID_CREDENTIALS`).
    계정이 없는지 비밀번호가 틀렸는지 구분하지 않는다.
    """
    return ok(await service.login(session, body.email, body.password))


@router.post(
    "/token/refresh",
    summary="액세스 토큰을 재발급한다",
    response_model=Envelope[TokenResponse],
    responses=raises(errors.RefreshTokenInvalid, errors.AccountBanned),
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
    """바꿀 수 있는 것은 `role`(writer · aspiring_writer · reader) 하나다. 닉네임은 바꿀 수 없다."""
    return ok(await service.update_me(session, user.id, body.role))


@users_router.get(
    "/check-username",
    summary="닉네임 입력 가능 여부를 확인한다 (기존 클라이언트 호환)",
    deprecated=True,
    response_model=Envelope[UsernameCheckResponse],
)
async def check_username(
    session: Session, username: Annotated[str, Query(min_length=1, max_length=USERNAME_MAX)]
) -> dict[str, Any]:
    """가입 화면용. 로그인하지 않아도 부를 수 있다.

    닉네임 중복은 허용한다. 공백뿐인 입력만 available=false 이며 DB 중복 조회는 하지 않는다.
    """
    return ok(await service.check_username(session, username))
