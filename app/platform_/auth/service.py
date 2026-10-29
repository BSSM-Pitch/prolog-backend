"""AUTH — Google 2단계 가입 + 일반(아이디·비밀번호) 가입·로그인.

Google authorization code 는 1회용이다. 신규 사용자에게 400 을 던지고 같은 code 로
재요청하게 하면 Google 이 거부하므로, 인증(=티켓 발급)과 가입 완료를 두 호출로 나눈다.
일반 가입은 티켓 없이 `/auth/signup` 에 email·password 를 직접 보낸다.
"""

import re
from collections.abc import Sequence
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
    hash_password,
    new_opaque_token,
    sha256,
    verify_password,
)
from app.platform_.auth import repository as repo
from app.platform_.auth.google import GoogleIdentity as OAuthIdentity
from app.platform_.auth.models import EMAIL_UQ, PROVIDER_UQ, USERNAME_UQ, User
from app.platform_.auth.schemas import (
    SessionResponse,
    SignupRequest,
    SignupTicketResponse,
    TokenResponse,
    UsernameCheckResponse,
    UserResponse,
)

PROVIDER = "google"
NAVER = "naver"
LOCAL = "local"
# 프론트 validation.ts 의 PASSWORD_RULE 과 같다.
PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


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


async def _issue(session: AsyncSession, user: User) -> TokenResponse:
    plain, token_hash = new_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    await repo.add_refresh_token(session, user.id, token_hash, expires_at)
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=plain,
        expires_in=settings.access_token_ttl_seconds,
    )


async def oauth_login(
    session: AsyncSession, provider: str, identity: OAuthIdentity
) -> tuple[SessionResponse | SignupTicketResponse, bool]:
    """(응답, is_new_user). 신규면 계정을 만들지 않고 가입 티켓만 준다. 구글·네이버 공통."""
    user = await repo.get_user_by_provider(session, provider, identity.sub)
    if user is not None:
        return SessionResponse(user=_user_response(user), tokens=await _issue(session, user)), False
    ticket = create_signup_ticket(identity.sub, identity.email, provider)
    return SignupTicketResponse(signup_ticket=ticket, email=identity.email), True


async def signup(session: AsyncSession, body: SignupRequest) -> SessionResponse:
    if body.signup_ticket:
        claims = decode_signup_ticket(body.signup_ticket)
    elif body.email is None or body.password is None:
        raise errors.InvalidInput("signup_ticket 또는 email·password 가 필요합니다")
    elif not PASSWORD_RULE.match(body.password):
        raise errors.InvalidInput("비밀번호는 영문과 숫자를 포함해 8자 이상이어야 합니다")
    username = (body.username or "").strip()
    if not username:
        raise errors.UsernameRequired()

    if body.signup_ticket:
        user = User(
            email=claims.get("email"),
            auth_provider=claims.get("provider", PROVIDER),
            provider_user_id=claims["sub"],
            username=username,
            role=body.role,
        )
    else:
        # ponytail: 이메일 소유 확인(인증 코드) 없이 가입한다.
        # 메일 발송을 붙일 때 여기서 코드를 검증한다.
        user = User(
            email=body.email,
            auth_provider=LOCAL,
            provider_user_id=username,
            password_hash=hash_password(body.password or ""),
            username=username,
            role=body.role,
        )
    try:
        await repo.add_user(session, user)
    except IntegrityError as exc:
        constraint = errors.constraint_name(exc)
        # local 의 provider_user_id 는 username 이라 같은 경합이 PROVIDER_UQ 로도 온다.
        if constraint == USERNAME_UQ or (constraint == PROVIDER_UQ and user.auth_provider == LOCAL):
            # 사전 SELECT 로는 경합을 막을 수 없어 제약을 신뢰한다.
            raise errors.UsernameTaken() from exc
        if constraint == EMAIL_UQ:
            raise errors.EmailTaken() from exc
        if constraint == PROVIDER_UQ:
            # ASSUMPTION: 이미 가입이 끝난 티켓의 재사용. 명세에 전용 코드가 없어
            # 티켓 무효로 답한다.
            raise errors.SignupTicketInvalid("이미 가입이 완료된 티켓입니다") from exc
        raise
    return SessionResponse(user=_user_response(user), tokens=await _issue(session, user))


async def login(session: AsyncSession, login_id: str, password: str) -> SessionResponse:
    user = await repo.get_local_user(session, login_id.strip())
    if not verify_password(password, user.password_hash if user else None) or user is None:
        raise errors.InvalidCredentials()
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


async def usernames_of(session: AsyncSession, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """조합 레이어(app/api)가 멤버 목록에 이름을 붙일 때 쓴다 (CLAUDE.md §6.3)."""
    return await repo.get_usernames(session, user_ids)


async def find_user_id_by_email(session: AsyncSession, email: str) -> UUID | None:
    """초대 대상이 이미 가입한 사용자인지. 미가입이면 None."""
    user = await repo.get_user_by_email(session, email)
    return user.id if user else None
