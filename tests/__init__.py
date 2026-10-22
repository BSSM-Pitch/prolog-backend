import os
import secrets

# JWT_SECRET 은 기본값이 없다(app/core/config.py). conftest 가 app 을 import 하기 전에 —
# 패키지 __init__ 은 conftest 보다 먼저 돈다 — 테스트용 값을 넣는다.
# 환경변수로 이미 줬으면 그대로 둔다.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
