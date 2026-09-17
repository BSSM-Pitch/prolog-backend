"""Google 인증 포트.

code 교환과 ID 토큰 검증을 인터페이스로 가른다(CLAUDE.md §4.4). 테스트는 이 포트에
fake 를 주입하므로, 로컬 가입 같은 **두 번째 인증 경로를 만들지 않고도** 실제 프로덕션
경로를 그대로 통과한다.

네트워크 호출은 stdlib(urllib) 로 하고 스레드에 태운다. 이 어댑터 하나만 외부를 친다.
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from app.core.config import settings
from app.core.errors import OAuthProviderError

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class GoogleIdentity:
    """검증이 끝난 Google 신원. `sub` 가 계정 식별자다 — 이메일은 바뀔 수 있다."""

    sub: str
    email: str | None


class GoogleOAuth(Protocol):
    async def exchange(self, oauth_code: str) -> GoogleIdentity:
        """authorization code 를 교환하고 ID 토큰을 검증한다. code 는 1회용이다."""
        ...


class GoogleOAuthClient:
    def __init__(self) -> None:
        self._jwks = jwt.PyJWKClient(CERTS_URL)

    async def exchange(self, oauth_code: str) -> GoogleIdentity:
        return await asyncio.to_thread(self._exchange, oauth_code)

    def _exchange(self, oauth_code: str) -> GoogleIdentity:
        id_token = self._post_code(oauth_code)
        claims = self._verify(id_token)
        sub = claims.get("sub")
        if not sub:
            raise OAuthProviderError("ID 토큰에 sub 가 없습니다")
        return GoogleIdentity(sub=str(sub), email=claims.get("email"))

    def _post_code(self, oauth_code: str) -> str:
        body = urllib.parse.urlencode(
            {
                "code": oauth_code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode()
        request = urllib.request.Request(TOKEN_ENDPOINT, data=body)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as res:
                payload = json.load(res)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise OAuthProviderError("Google 토큰 교환에 실패했습니다") from exc
        id_token = payload.get("id_token")
        if not id_token:
            raise OAuthProviderError("Google 응답에 id_token 이 없습니다")
        return str(id_token)

    def _verify(self, id_token: str) -> dict[str, Any]:
        """서명 · aud · iss · exp 를 모두 검증한다 (CLAUDE.md §4.3)."""
        try:
            key = self._jwks.get_signing_key_from_jwt(id_token).key
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=settings.google_client_id,
                options={"require": ["exp", "aud", "iss", "sub"]},
            )
        except (jwt.PyJWTError, urllib.error.URLError, OSError) as exc:
            raise OAuthProviderError("Google ID 토큰 검증에 실패했습니다") from exc
        # PyJWT 의 issuer 인자는 단일 문자열이라 Google 의 두 표기를 한 번에 못 준다.
        if claims.get("iss") not in ISSUERS:
            raise OAuthProviderError("Google ID 토큰의 iss 가 올바르지 않습니다")
        return claims


_client = GoogleOAuthClient()


def google_oauth() -> GoogleOAuth:
    """FastAPI 의존성. 테스트는 이 함수를 dependency_overrides 로 갈아끼운다."""
    return _client
