from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    new_opaque_token,
    sha256,
    verify_password,
)
from app.platform_.auth import repository as repo
from app.platform_.auth.models import User
from app.platform_.auth.schemas import (
    LoginRequest,
    SessionResponse,
    SignupRequest,
    TokenResponse,
    UserResponse,
)


def _user_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


async def _issue(session: AsyncSession, user: User) -> TokenResponse:
    plain, token_hash = new_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    await repo.add_refresh_token(session, user.id, token_hash, expires_at)
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=plain,
        expires_in=settings.access_token_ttl_seconds,
    )


async def signup(session: AsyncSession, body: SignupRequest) -> SessionResponse:
    user = User(
        email=str(body.email),
        password_hash=hash_password(body.password),
        auth_provider="local",
        nickname=body.nickname,
        role=body.role,
    )
    try:
        await repo.add_user(session, user)
    except IntegrityError as exc:
        # lower(email) UNIQUE 위반. 사전 SELECT 로는 경합을 막을 수 없어 제약을 신뢰한다.
        raise errors.EmailAlreadyExists() from exc
    return SessionResponse(user=_user_response(user), token=await _issue(session, user))


async def login(session: AsyncSession, body: LoginRequest) -> SessionResponse:
    user = await repo.get_user_by_email(session, str(body.email))
    if user is None or user.password_hash is None:
        raise errors.InvalidCredentials()
    if not verify_password(body.password, user.password_hash):
        raise errors.InvalidCredentials()
    return SessionResponse(user=_user_response(user), token=await _issue(session, user))


async def refresh(session: AsyncSession, refresh_token: str) -> TokenResponse:
    row = await repo.get_refresh_token(session, sha256(refresh_token))
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise errors.InvalidRefreshToken()
    user = await repo.get_user(session, row.user_id)
    if user is None:
        raise errors.InvalidRefreshToken()
    await repo.revoke_refresh_token(session, row.token_hash)  # 로테이션
    return await _issue(session, user)


async def logout(session: AsyncSession, refresh_token: str) -> None:
    await repo.revoke_refresh_token(session, sha256(refresh_token))


async def me(session: AsyncSession, user_id: UUID) -> UserResponse:
    user = await repo.get_user(session, user_id)
    if user is None:
        raise errors.UserNotFound()
    return _user_response(user)
