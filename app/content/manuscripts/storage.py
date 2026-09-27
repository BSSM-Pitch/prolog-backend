"""오브젝트 스토리지 포트.

업로드는 **presigned URL** 이다 — 서버가 파일 바이트를 통과시키지 않는다(ROADMAP Phase 1).
그래서 서버는 용량을 볼 수 없고, `FILE_TOO_LARGE` 는 이 경로에서 발생하지 않는다.

DB 에는 **key 만** 저장한다. URL 은 만료되므로 저장하면 곧 거짓이 된다 (CLAUDE.md §7).
"""

from typing import NamedTuple, Protocol
from uuid import UUID

import boto3

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


def object_key(project_id: UUID, manuscript_id: UUID, file_format: str) -> str:
    return f"manuscripts/{project_id}/{manuscript_id}.{file_format}"


def content_type_of(file_format: str) -> str:
    return _CONTENT_TYPES[file_format]


class S3Storage:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    def presigned_put(self, key: str, content_type: str) -> PresignedUpload:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=settings.presigned_ttl_seconds,
        )
        return PresignedUpload(url=str(url), expires_in=settings.presigned_ttl_seconds)


_storage = S3Storage()


def storage() -> Storage:
    """FastAPI 의존성. 테스트는 dependency_overrides 로 갈아끼운다."""
    return _storage
