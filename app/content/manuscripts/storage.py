"""오브젝트 스토리지 포트.

업로드는 **presigned URL** 이다 — 서버가 파일 바이트를 통과시키지 않는다(ROADMAP Phase 1).
그래서 서버는 용량을 볼 수 없고, `FILE_TOO_LARGE` 는 이 경로에서 발생하지 않는다.

DB 에는 **key 만** 저장한다. URL 은 만료되므로 저장하면 곧 거짓이 된다 (CLAUDE.md §7).
"""

from typing import NamedTuple, Protocol
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

# 명세 §2.1 의 `file_format`.
SUPPORTED_FORMATS = ("docx", "txt", "pdf")
_CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "pdf": "application/pdf",
}


class PresignedUpload(NamedTuple):
    url: str
    expires_in: int


class Storage(Protocol):
    def presigned_put(self, key: str, content_type: str) -> PresignedUpload:
        """클라이언트가 직접 PUT 할 URL. 서버는 이 URL 을 저장하지 않는다."""
        ...

    def exists(self, key: str) -> bool:
        """업로드 완료 콜백이 객체 존재를 확인할 때 쓴다 (head_object)."""
        ...


def object_key(project_id: UUID, manuscript_id: UUID, file_format: str) -> str:
    return f"manuscripts/{project_id}/{manuscript_id}.{file_format}"


def content_type_of(file_format: str) -> str:
    return _CONTENT_TYPES[file_format]


class S3Storage:
    def __init__(self) -> None:
        # 로컬 S3 호환 서버는 가상호스트 주소(bucket.host)를 못 받는다. endpoint 가 있을 때만
        # path 스타일로 돌린다 — AWS 로 갈 때는 기본값(가상호스트)을 그대로 쓴다.
        config = Config(s3={"addressing_style": "path"}) if settings.s3_endpoint_url else None
        self._client = boto3.client(
            "s3",
            config=config,
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        """로컬 S3 호환 서버에만 버킷을 만든다(멱등). AWS 에서는 손대지 않는다 —
        앱이 프로덕션 버킷을 만들 권한을 가질 이유가 없다."""
        if not settings.s3_endpoint_url or self._bucket_ready:
            return
        try:
            self._client.create_bucket(Bucket=settings.s3_bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in (
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            ):
                raise
        self._bucket_ready = True

    def presigned_put(self, key: str, content_type: str) -> PresignedUpload:
        self._ensure_bucket()
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=settings.presigned_ttl_seconds,
        )
        return PresignedUpload(url=str(url), expires_in=settings.presigned_ttl_seconds)

    def exists(self, key: str) -> bool:
        self._ensure_bucket()
        try:
            self._client.head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


_storage = S3Storage()


def storage() -> Storage:
    """FastAPI 의존성. 테스트는 dependency_overrides 로 갈아끼운다."""
    return _storage
