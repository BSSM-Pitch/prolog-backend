"""실제 Google 검증 경로(GoogleOAuthClient._verify) — FakeGoogle 이 건너뛰는 부분.

RS256 서명을 실제로 만들고 검증한다. `cryptography` 가 빠지면(pyjwt[crypto]) 여기서 깨진다 —
2026-10-01 실제 Google 로그인이 이것 때문에 502 였는데 FakeGoogle 테스트는 전부 통과했다.
"""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings
from app.core.errors import OAuthProviderError
from app.platform_.auth.google import GoogleOAuthClient

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Any:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = GoogleOAuthClient()
    jwk = jwt.PyJWK.from_dict(
        {
            **jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
            "kid": "k1",
            "alg": "RS256",
        }
    )
    monkeypatch.setattr(client._jwks, "get_signing_key_from_jwt", lambda token: jwk)
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)

    def sign(**overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "1234567890",
            "email": "writer@example.com",
            "iat": now,
            "exp": now + 3600,
        } | overrides
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})

    return client, sign


def test_valid_google_id_token(signer: Any) -> None:
    client, sign = signer
    claims = client._verify(sign())
    assert (claims["sub"], claims["email"]) == ("1234567890", "writer@example.com")


def test_server_clock_behind_google_is_tolerated(signer: Any) -> None:
    client, sign = signer
    # 서버 시계가 30초 느리면 iat 가 30초 미래로 보인다
    assert client._verify(sign(iat=int(time.time()) + 30))["sub"] == "1234567890"


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 3600},
    ],
)
def test_rejects_wrong_aud_iss_or_expired(signer: Any, bad: dict[str, Any]) -> None:
    client, sign = signer
    with pytest.raises(OAuthProviderError):
        client._verify(sign(**bad))
