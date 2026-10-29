"""네이버 인증 포트.

구글과 같은 2단계 가입을 탄다(code → 티켓 → `/auth/signup`). 네이버는 ID 토큰이 없어서
code 를 access token 으로 바꾼 뒤 프로필 API 로 `id`(앱별 불변 식별자)를 받는다.
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import OAuthProviderError
from app.platform_.auth.google import GoogleIdentity as OAuthIdentity

TOKEN_ENDPOINT = "https://nid.naver.com/oauth2.0/token"
PROFILE_ENDPOINT = "https://openapi.naver.com/v1/nid/me"
_TIMEOUT_SECONDS = 5


class NaverOAuth(Protocol):
    async def exchange(self, oauth_code: str, state: str | None) -> OAuthIdentity:
        """authorization code 를 교환하고 프로필을 읽는다. code 는 1회용이다."""
        ...


class NaverOAuthClient:
    async def exchange(self, oauth_code: str, state: str | None) -> OAuthIdentity:
        return await asyncio.to_thread(self._exchange, oauth_code, state)

    def _exchange(self, oauth_code: str, state: str | None) -> OAuthIdentity:
        query = {
            "grant_type": "authorization_code",
            "client_id": settings.naver_client_id,
            "client_secret": settings.naver_client_secret,
            "code": oauth_code,
            "state": state or "",
        }
        token = _get_json(f"{TOKEN_ENDPOINT}?{urllib.parse.urlencode(query)}").get("access_token")
        if not token:
            raise OAuthProviderError("네이버 토큰 교환에 실패했습니다")
        profile = _get_json(PROFILE_ENDPOINT, {"Authorization": f"Bearer {token}"})
        response = profile.get("response") or {}
        if profile.get("resultcode") != "00" or not response.get("id"):
            raise OAuthProviderError("네이버 프로필 조회에 실패했습니다")
        return OAuthIdentity(sub=str(response["id"]), email=response.get("email"))


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as res:
            payload: dict[str, Any] = json.load(res)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise OAuthProviderError("네이버 응답을 받지 못했습니다") from exc
    return payload


_client = NaverOAuthClient()


def naver_oauth() -> NaverOAuth:
    """FastAPI 의존성. 테스트는 이 함수를 dependency_overrides 로 갈아끼운다."""
    return _client
