"""추출 워커 — 포트 단위 + 잡 처리."""

import zipfile
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts.extraction import FileExtractor, UnsupportedFormat
from app.content.manuscripts.job_handler import handle
from app.content.manuscripts.service import EXTRACTION_JOB_TYPE
from app.db.session import SessionFactory
from app.events.models import OutboxEvent
from app.events.relay import route
from app.jobs import service as jobs
from tests.conftest import FakeStorage, signup

DOCX_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>1화. </w:t></w:r><w:r><w:t>거미줄 너머</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>2화.</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


def _docx(xml: str = DOCX_XML) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_txt_and_docx_extraction() -> None:
    extractor = FileExtractor()
    assert extractor.extract("txt", "본문입니다".encode()) == "본문입니다"
    # 문단은 줄로, 문단 안의 런은 이어 붙인다.
    assert extractor.extract("docx", _docx()) == "1화. 거미줄 너머\n2화."


def test_unsupported_format_raises() -> None:
    with pytest.raises(UnsupportedFormat):
        FileExtractor().extract("pdf", b"%PDF-1.7")


def test_relay_routes_job_events_to_their_queue() -> None:
    """한 큐에 notifier 와 extractor 를 함께 붙이면 서로의 메시지를 먹는다."""
    job_event = OutboxEvent(
        aggregate_type="job",
        aggregate_id=uuid4(),
        event_type="job.queued",
        payload={"queue": "io"},
    )
    domain_event = OutboxEvent(
        aggregate_type="team", aggregate_id=uuid4(), event_type="team.invited", payload={}
    )
    assert route(job_event) == "io"
    assert route(domain_event) == "notify"


async def _queued_extraction(client: AsyncClient, email: str, data: bytes, fmt: str) -> dict:
    """업로드 원고를 만들고 콜백까지 태워 queued 잡을 만든다."""
    user = await signup(client, email)
    pid = (
        await client.post(
            "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    mid = (
        await client.post(
            f"/projects/{pid}/manuscripts",
            json={"title": "초고", "source_type": "upload"},
            headers=user["headers"],
        )
    ).json()["data"]["manuscript_id"]
    issued = (
        await client.post(
            f"/projects/{pid}/manuscripts/{mid}/file",
            json={"file_format": fmt},
            headers=user["headers"],
        )
    ).json()["data"]
    FakeStorage.uploaded[issued["file_key"]] = data
    done = (
        await client.post(
            f"/projects/{pid}/manuscripts/{mid}/file/complete", headers=user["headers"]
        )
    ).json()["data"]
    return {"user": user, "pid": pid, "mid": mid, "job_id": done["extraction_job_id"]}


async def test_extraction_fills_content_and_completes_the_job(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _queued_extraction(client, "ex1@example.com", _docx(), "docx")

    handled = await handle(SessionFactory, UUID(ctx["job_id"]), FakeStorage(), FileExtractor())
    assert handled == "completed"

    detail = (
        await client.get(
            f"/projects/{ctx['pid']}/manuscripts/{ctx['mid']}", headers=ctx["user"]["headers"]
        )
    ).json()["data"]
    assert detail["status"] == "ready"
    assert detail["content"] == "1화. 거미줄 너머\n2화."
    # 챕터는 자동 분할하지 않는다 — 명세에 분할 규칙이 없다.
    assert detail["chapter_count"] == 0

    job = await jobs.get(db, UUID(ctx["job_id"]))
    assert job is not None
    assert job.status == "completed"
    assert job.result == {"chars": len("1화. 거미줄 너머\n2화.")}
    assert job.attempt == 1

    # at-least-once — 같은 메시지가 또 와도 선점(조건부 UPDATE)에서 진다.
    again = await handle(SessionFactory, UUID(ctx["job_id"]), FakeStorage(), FileExtractor())
    assert again == "skipped"


async def test_extraction_failure_lands_on_the_job_not_the_manuscript(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _queued_extraction(client, "ex2@example.com", b"not-a-zip", "docx")

    result = await handle(SessionFactory, UUID(ctx["job_id"]), FakeStorage(), FileExtractor())
    # 파일 탓인 실패는 다시 해도 같다 — 재시도하지 않는다.
    assert result == "failed"

    job = await jobs.get(db, UUID(ctx["job_id"]))
    assert job is not None
    assert job.status == "failed"
    assert job.error is not None
    assert job.error["code"] == "TEXT_EXTRACTION_FAILED"

    detail = (
        await client.get(
            f"/projects/{ctx['pid']}/manuscripts/{ctx['mid']}", headers=ctx["user"]["headers"]
        )
    ).json()["data"]
    # 원고에는 상태만 남는다. 사유는 잡에 있다 — 상태를 두 곳에 두지 않는다.
    assert detail["status"] == "failed"
    assert detail["content"] is None
    assert "error" not in detail


async def test_unsupported_format_is_recorded_on_the_job(
    client: AsyncClient, db: AsyncSession
) -> None:
    """발급 단계가 pdf 를 막으므로 HTTP 로는 도달하지 않는다.

    그래도 분기는 남겨 둔다 — 지원 목록이 줄어들면 이미 큐에 있던 잡이 여기로 온다.
    그래서 잡을 직접 만들어 확인한다.
    """
    ctx = await _queued_extraction(client, "ex3@example.com", b"ignored", "txt")
    stale = await jobs.create(
        db,
        project_id=UUID(ctx["pid"]),
        job_type=EXTRACTION_JOB_TYPE,
        target_type="manuscript",
        target_id=UUID(ctx["mid"]),
        queue="io",
        input={"file_key": "manuscripts/x.pdf", "file_format": "pdf"},
    )
    await db.commit()
    FakeStorage.uploaded["manuscripts/x.pdf"] = b"%PDF-1.7"

    assert await handle(SessionFactory, stale.id, FakeStorage(), FileExtractor()) == "failed"

    await db.refresh(stale)
    job = stale
    assert job is not None
    assert job.error == {"code": "UNSUPPORTED_FILE_FORMAT", "format": "pdf"}
