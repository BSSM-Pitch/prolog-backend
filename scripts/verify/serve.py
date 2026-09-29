"""E2E 검증용 서버 — `uv run python -m scripts.verify.serve`.

Google 포트만 fake 로 갈아끼우고 나머지(PG · elasticmq · s3mock)는 .env 그대로 실물을 쓴다.
fake 는 tests/conftest.py 의 FakeGoogle 과 같은 규칙이다: ``oauth_code = "{sub}|{email}"``.

**로컬 검증 전용이다.** 이 프로세스는 아무 이메일로나 로그인시켜 준다. 배포에 쓰지 않는다.
"""

import uvicorn

from app.main import app
from app.platform_.auth.google import GoogleIdentity, google_oauth

PORT = 8001


class FakeGoogle:
    async def exchange(self, oauth_code: str) -> GoogleIdentity:
        sub, _, email = oauth_code.partition("|")
        return GoogleIdentity(sub=sub, email=email or None)


if __name__ == "__main__":
    app.dependency_overrides[google_oauth] = FakeGoogle
    uvicorn.run(app, host="127.0.0.1", port=PORT)
