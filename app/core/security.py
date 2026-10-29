"""토큰 발급·검증.

비밀번호는 stdlib scrypt 로 해시한다(local 계정만). 평문은 저장하지 않는다.

액세스 토큰과 가입 티켓은 같은 키로 서명되므로 **`aud` 로 용도를 가른다.**
가입 티켓을 ``Authorization: Bearer`` 로 제출하면 `aud` 불일치로 401 이 된다.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from app.core.config import settings
from app.core.errors import SignupTicketInvalid, Unauthorized

ACCESS_AUD = "storyforge:access"
SIGNUP_AUD = "storyforge:signup"


def _encode(payload: dict[str, Any], aud: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    claims = {
        **payload,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str, aud: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=aud,
        options={"require": ["exp", "aud", "sub"]},
    )


def create_access_token(user_id: UUID, email: str | None) -> str:
    return _encode(
        {"sub": str(user_id), "email": email}, ACCESS_AUD, settings.access_token_ttl_seconds
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return _decode(token, ACCESS_AUD)
    except jwt.PyJWTError as exc:
        raise Unauthorized("토큰이 유효하지 않습니다") from exc


def create_signup_ticket(provider_user_id: str, email: str | None, provider: str) -> str:
    """TTL 10분. 계정을 만들지 않고 'Google 인증은 끝났다'만 증명한다 (AUTH 계약 §4.2)."""
    return _encode(
        {"sub": provider_user_id, "email": email, "provider": provider},
        SIGNUP_AUD,
        settings.signup_ticket_ttl_seconds,
    )


def decode_signup_ticket(token: str) -> dict[str, Any]:
    try:
        return _decode(token, SIGNUP_AUD)
    except jwt.PyJWTError as exc:
        raise SignupTicketInvalid() from exc


def new_opaque_token() -> tuple[str, str]:
    """(평문, sha256) — 평문은 클라이언트에게만, DB 에는 해시만 저장한다."""
    plain = secrets.token_urlsafe(48)
    return plain, sha256(plain)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# n=2^14·r=8 → 해시 1회 16MB·수십 ms. 형식 `scrypt$salt$digest` — 파라미터를 바꾸면 접두어를 바꾼다.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


_DUMMY_HASH = hash_password("")


def verify_password(password: str, stored: str | None) -> bool:
    """없는 계정(stored=None)도 해시를 한 번 돌린다 — 응답 시간으로 아이디 존재를 알 수 없게."""
    _, salt, digest = (stored or _DUMMY_HASH).split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return stored is not None and hmac.compare_digest(actual.hex(), digest)
