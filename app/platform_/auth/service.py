"""AUTH v0.2 — Google 단일, 2단계 가입.

Google authorization code 는 1회용이다. 신규 사용자에게 400 을 던지고 같은 code 로
재요청하게 하면 Google 이 거부하므로, 인증(=티켓 발급)과 가입 완료를 두 호출로 나눈다.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_signup_ticket,
    decode_signup_ticket,
    new_opaque_token,
    sha256,
)
from app.platform_.auth import repository as repo
from app.platform_.auth.google import GoogleOAuth
from app.platform_.auth.models import PROVIDER_UQ, USERNAME_UQ, User
from app.platform_.auth.schemas import (
    SessionResponse,
    SignupRequest,
    SignupTicketResponse,
    TokenResponse,
    UsernameCheckResponse,
    UserResponse,
)

PROVIDER = "google"


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        auth_provider=user.auth_provider,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    """제약 **이름**으로 분기한다. 메시지 문자열 파싱은 PG 마이너 버전에 깨진다(CLAUDE.md §6)."""
    err: BaseException | None = exc.orig
    while err is not None:
        name = getattr(err, "constraint_name", None)
        if name:
            return str(name)
        err = err.__cause__
    return None


async def _issue(session: AsyncSession, user: User) -> TokenResponse:
    plain, token_hash = new_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    await repo.add_refresh_token(session, user.id, token_hash, expires_at)
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=plain,
        expires_in=settings.access_token_ttl_seconds,
    )


async def oauth_google(
    session: AsyncSession, google: GoogleOAuth, oauth_code: str
) -> tuple[SessionResponse | SignupTicketResponse, bool]:
    """(응답, is_new_user). 신규면 계정을 만들지 않고 가입 티켓만 준다."""
    identity = await google.exchange(oauth_code)
    user = await repo.get_user_by_provider(session, PROVIDER, identity.sub)
    if user is not None:
        return SessionResponse(user=_user_response(user), tokens=await _issue(session, user)), False
    ticket = create_signup_ticket(identity.sub, identity.email, PROVIDER)
    return SignupTicketResponse(signup_ticket=ticket, email=identity.email), True


async def signup(session: AsyncSession, body: SignupRequest) -> SessionResponse:
    claims = decode_signup_ticket(body.signup_ticket)
    username = (body.username or "").strip()
    if not username:
        raise errors.UsernameRequired()

    user = User(
        email=claims.get("email"),
        auth_provider=claims.get("provider", PROVIDER),
        provider_user_id=claims["sub"],
        username=username,
        role=body.role,
    )
    try:
        await repo.add_user(session, user)
    except IntegrityError as exc:
        constraint = _constraint_name(exc)
        if constraint == USERNAME_UQ:
            # 사전 SELECT 로는 경합을 막을 수 없어 제약을 신뢰한다.
            raise errors.UsernameTaken() from exc
        if constraint == PROVIDER_UQ:
            # ASSUMPTION: 이미 가입이 끝난 티켓의 재사용. 명세에 전용 코드가 없어
            # 티켓 무효로 답한다.
            raise errors.SignupTicketInvalid("이미 가입이 완료된 티켓입니다") from exc
        raise
    return SessionResponse(user=_user_response(user), tokens=await _issue(session, user))


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    row = await repo.get_refresh_token(session, sha256(refresh_token))
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise errors.RefreshTokenInvalid()
    user = await repo.get_user(session, row.user_id)
    if user is None:
        raise errors.RefreshTokenInvalid()
    await repo.revoke_refresh_token(session, row.token_hash)  # 로테이션: 재사용은 거부된다
    return await _issue(session, user)


async def logout(session: AsyncSession, refresh_token: str) -> None:
    await repo.revoke_refresh_token(session, sha256(refresh_token))


async def _get(session: AsyncSession, user_id: UUID) -> User:
    user = await repo.get_user(session, user_id)
    if user is None:
        raise errors.UserNotFound()
    return user


async def me(session: AsyncSession, user_id: UUID) -> UserResponse:
    return _user_response(await _get(session, user_id))


async def update_me(session: AsyncSession, user_id: UUID, role: str) -> UserResponse:
    user = await _get(session, user_id)
    user.role = role
    await session.flush()
    return _user_response(user)


async def check_username(session: AsyncSession, username: str) -> UsernameCheckResponse:
    taken = await repo.username_exists(session, username)
    return UsernameCheckResponse(username=username, available=not taken)
