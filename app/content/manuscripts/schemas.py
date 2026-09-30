from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# DDL 이 정본이다 (CLAUDE.md §7). 명세의 `file`·`extraction_failed` 가 아니다.
SourceType = Literal["editor", "upload"]
ManuscriptStatus = Literal["draft", "processing", "ready", "failed"]


class ManuscriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_type: SourceType = "editor"


class ManuscriptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = None
    # 명세 §4.4 는 변경 **시도** 에 409 를 요구한다. 스키마에서 막으면 400 이 나가므로 받는다.
    source_type: SourceType | None = None


class ManuscriptResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    manuscript_id: UUID = Field(validation_alias="id")
    project_id: UUID
    title: str
    source_type: SourceType
    # 명세는 `file_url` 이지만 DB 에는 key 만 둔다 — URL 은 만료된다 (§7).
    file_key: str | None
    content: str | None
    # 같은 모듈의 chapters 를 세어 채운다.
    chapter_count: int = 0
    status: ManuscriptStatus
    # 실패 사유는 여기가 아니라 이 잡에 있다. 상태를 두 곳에 두지 않는다.
    extraction_job_id: UUID | None
    created_at: datetime
    updated_at: datetime


class UploadRequest(BaseModel):
    # Literal 로 막으면 400 INVALID_INPUT 이 나간다. 명세 §1.4 는 UNSUPPORTED_FILE_FORMAT
    # 을 요구하므로 문자열로 받고 서비스에서 검사한다.
    file_format: str = Field(min_length=1, max_length=10)


class UploadResponse(BaseModel):
    """presigned URL 발급 결과. 실제 업로드는 클라이언트가 S3 로 직접 한다.

    **추출 잡은 여기서 만들지 않는다.** 파일이 아직 없기 때문이다 — 업로드를 마친 클라이언트가
    `.../file/complete` 를 부르면 그때 객체 존재를 확인하고 큐에 넣는다.
    """

    manuscript_id: UUID
    file_key: str
    upload_url: str
    expires_in: int
    status: ManuscriptStatus


class ChapterCreate(BaseModel):
    manuscript_id: UUID
    chapter_no: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    content: str = ""


class ChapterUpdate(BaseModel):
    chapter_no: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None


class ChapterResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_id: UUID = Field(validation_alias="id")
    manuscript_id: UUID
    chapter_no: int
    title: str | None
    content: str


class ManuscriptVersionResponse(BaseModel):
    """명세 §4.10 "자동저장 스냅샷". 화면 32 가 고른 스냅샷의 본문을 보여주므로 본문을 담는다."""

    version_id: UUID
    manuscript_id: UUID
    version_no: int
    # editor(편집기 저장 — 5분 창으로 묶인다) | upload(파일 추출 결과)
    source: Literal["editor", "upload"]
    char_count: int
    content: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime = Field(description="같은 5분 창의 저장이 덮어쓴 마지막 시각")
