"""MSU · 챕터.

DDL 이 정본이다(CLAUDE.md §7): source_type 은 editor|upload, 파일은 file_key,
status 는 draft|processing|ready|failed. 챕터 경로는 프로젝트 직속이다(SCDS 기준).
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import service as jobs
from tests.conftest import code, signup


async def _project(client: AsyncClient, email: str) -> tuple[dict, str]:
    user = await signup(client, email)
    project = (
        await client.post(
            "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]
    return user, project["project_id"]


async def _manuscript(client: AsyncClient, user: dict, pid: str, **kwargs: object) -> dict:
    body = {"title": "원고", "source_type": "editor"} | kwargs
    res = await client.post(f"/projects/{pid}/manuscripts", json=body, headers=user["headers"])
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def test_editor_manuscript_is_ready_with_empty_body(client: AsyncClient) -> None:
    user, pid = await _project(client, "ms1@example.com")
    data = await _manuscript(client, user, pid)

    assert set(data) == {
        "manuscript_id",
        "project_id",
        "title",
        "source_type",
        "file_key",
        "content",
        "chapter_count",
        "status",
        "extraction_job_id",
        "created_at",
        "updated_at",
    }
    assert data["source_type"] == "editor"
    assert data["status"] == "ready"
    assert data["content"] == ""
    assert data["file_key"] is None
    assert data["chapter_count"] == 0
    assert data["extraction_job_id"] is None

    listed = await client.get(f"/projects/{pid}/manuscripts", headers=user["headers"])
    assert [m["manuscript_id"] for m in listed.json()["data"]] == [data["manuscript_id"]]
    assert listed.json()["meta"]["next_cursor"] is None


async def test_autosave_patch_and_immutable_source_type(client: AsyncClient) -> None:
    user, pid = await _project(client, "ms2@example.com")
    ms = await _manuscript(client, user, pid)
    url = f"/projects/{pid}/manuscripts/{ms['manuscript_id']}"

    saved = await client.patch(url, json={"content": "1화. ..."}, headers=user["headers"])
    assert saved.json()["data"]["content"] == "1화. ..."

    # 명세 §4.4: 변경 **시도** 가 409 다.
    denied = await client.patch(url, json={"source_type": "upload"}, headers=user["headers"])
    assert denied.status_code == 409
    assert code(denied) == "SOURCE_TYPE_IMMUTABLE"

    # 같은 값이면 변경이 아니다.
    same = await client.patch(url, json={"source_type": "editor"}, headers=user["headers"])
    assert same.status_code == 200


async def test_upload_issues_presigned_url_and_extraction_job(
    client: AsyncClient, db: AsyncSession
) -> None:
    user, pid = await _project(client, "ms3@example.com")
    ms = await _manuscript(client, user, pid, source_type="upload", title="초고")
    assert ms["status"] == "draft"
    assert ms["content"] is None

    res = await client.post(
        f"/projects/{pid}/manuscripts/{ms['manuscript_id']}/file",
        json={"file_format": "docx"},
        headers=user["headers"],
    )
    assert res.status_code == 202
    data = res.json()["data"]
    assert data["upload_url"].startswith("https://s3.test/")
    assert data["file_key"] == f"manuscripts/{pid}/{ms['manuscript_id']}.docx"
    assert data["expires_in"] == 600
    # 파일이 아직 없다. 잡을 걸지 않고 draft 에 머문다.
    assert data["status"] == "draft"
    assert "extraction_job_id" not in data

    detail = await client.get(
        f"/projects/{pid}/manuscripts/{ms['manuscript_id']}", headers=user["headers"]
    )
    # 응답은 key 만 준다. URL 은 만료되므로 저장하지 않는다.
    assert detail.json()["data"]["file_key"] == data["file_key"]
    assert detail.json()["data"]["extraction_job_id"] is None


async def test_upload_rejects_bad_format_and_editor_manuscript(client: AsyncClient) -> None:
    user, pid = await _project(client, "ms4@example.com")
    upload = await _manuscript(client, user, pid, source_type="upload")
    editor = await _manuscript(client, user, pid, title="에디터")

    for fmt in ("hwp", "pdf"):
        # pdf 는 추출기가 읽지 못한다. 발급 허용 목록과 추출 가능 목록은 같은 상수다 —
        # 올린 뒤에 반드시 실패하느니 업로드 시점에 거절한다.
        bad = await client.post(
            f"/projects/{pid}/manuscripts/{upload['manuscript_id']}/file",
            json={"file_format": fmt},
            headers=user["headers"],
        )
        assert bad.status_code == 400, fmt
        assert code(bad) == "UNSUPPORTED_FILE_FORMAT"
        assert bad.json()["error"]["details"]["supported"] == ["txt", "docx"]

    wrong = await client.post(
        f"/projects/{pid}/manuscripts/{editor['manuscript_id']}/file",
        json={"file_format": "docx"},
        headers=user["headers"],
    )
    assert wrong.status_code == 400
    assert code(wrong) == "INVALID_INPUT"


async def test_manuscript_is_scoped_to_its_project(client: AsyncClient) -> None:
    owner, pid = await _project(client, "ms5@example.com")
    ms = await _manuscript(client, owner, pid)
    _, other_pid = await _project(client, "ms6@example.com")

    # 다른 프로젝트 경로로는 찾을 수 없다(owner 는 두 프로젝트 모두의 멤버가 아니다).
    missing = await client.get(
        f"/projects/{pid}/manuscripts/00000000-0000-0000-0000-000000000000",
        headers=owner["headers"],
    )
    assert missing.status_code == 404
    assert code(missing) == "MANUSCRIPT_NOT_FOUND"
    assert (
        await client.get(f"/projects/{other_pid}/manuscripts", headers=owner["headers"])
    ).status_code == 403
    assert ms["project_id"] == pid


async def test_chapters_live_under_the_project(client: AsyncClient) -> None:
    """경로는 /projects/{p}/chapters/{c} 다 — SCDS 가 그렇게 참조한다."""
    user, pid = await _project(client, "ch1@example.com")
    ms = await _manuscript(client, user, pid)
    mid = ms["manuscript_id"]

    created = await client.post(
        f"/projects/{pid}/chapters",
        json={"manuscript_id": mid, "chapter_no": 3, "title": "3화", "content": "본문"},
        headers=user["headers"],
    )
    assert created.status_code == 201
    chapter = created.json()["data"]
    assert set(chapter) == {"chapter_id", "manuscript_id", "chapter_no", "title", "content"}
    assert chapter["chapter_no"] == 3

    # 같은 원고에 같은 화 번호는 안 된다.
    dup = await client.post(
        f"/projects/{pid}/chapters",
        json={"manuscript_id": mid, "chapter_no": 3, "content": ""},
        headers=user["headers"],
    )
    assert dup.status_code == 400
    assert code(dup) == "INVALID_INPUT"

    listed = await client.get(
        f"/projects/{pid}/chapters?manuscript_id={mid}", headers=user["headers"]
    )
    assert [c["chapter_no"] for c in listed.json()["data"]] == [3]

    patched = await client.patch(
        f"/projects/{pid}/chapters/{chapter['chapter_id']}",
        json={"title": "3화 개고"},
        headers=user["headers"],
    )
    assert patched.json()["data"]["title"] == "3화 개고"

    # 원고 응답의 chapter_count 는 같은 모듈에서 센다.
    detail = await client.get(f"/projects/{pid}/manuscripts/{mid}", headers=user["headers"])
    assert detail.json()["data"]["chapter_count"] == 1

    assert (
        await client.delete(
            f"/projects/{pid}/chapters/{chapter['chapter_id']}", headers=user["headers"]
        )
    ).status_code == 204
    gone = await client.get(
        f"/projects/{pid}/chapters/{chapter['chapter_id']}", headers=user["headers"]
    )
    assert gone.status_code == 404
    assert code(gone) == "CHAPTER_NOT_FOUND"


async def test_deleting_a_manuscript_removes_its_chapters(client: AsyncClient) -> None:
    user, pid = await _project(client, "ch2@example.com")
    ms = await _manuscript(client, user, pid)
    await client.post(
        f"/projects/{pid}/chapters",
        json={"manuscript_id": ms["manuscript_id"], "chapter_no": 1, "content": "x"},
        headers=user["headers"],
    )

    assert (
        await client.delete(
            f"/projects/{pid}/manuscripts/{ms['manuscript_id']}", headers=user["headers"]
        )
    ).status_code == 204
    assert (await client.get(f"/projects/{pid}/chapters", headers=user["headers"])).json()[
        "data"
    ] == []


async def test_upload_complete_creates_the_extraction_job(
    client: AsyncClient, db: AsyncSession
) -> None:
    """S3 PUT 을 마친 클라이언트가 콜백을 부른다. 잡은 이때 큐에 들어간다."""
    from tests.conftest import FakeStorage

    user, pid = await _project(client, "ms7@example.com")
    ms = await _manuscript(client, user, pid, source_type="upload")
    mid = ms["manuscript_id"]
    issued = (
        await client.post(
            f"/projects/{pid}/manuscripts/{mid}/file",
            json={"file_format": "docx"},
            headers=user["headers"],
        )
    ).json()["data"]

    # 아직 안 올렸다 — head_object 가 못 찾는다.
    early = await client.post(
        f"/projects/{pid}/manuscripts/{mid}/file/complete", headers=user["headers"]
    )
    assert early.status_code == 400
    assert code(early) == "INVALID_INPUT"

    FakeStorage.uploaded[issued["file_key"]] = "1화.".encode()
    done = await client.post(
        f"/projects/{pid}/manuscripts/{mid}/file/complete", headers=user["headers"]
    )
    assert done.status_code == 200
    data = done.json()["data"]
    assert data["status"] == "processing"

    job = await jobs.get(db, UUID(data["extraction_job_id"]))
    assert job is not None
    assert job.job_type == "manuscript_extraction"
    assert job.queue == "io"
    assert job.status == "queued"
    assert job.input == {"file_key": issued["file_key"], "file_format": "docx"}

    # 두 번 불러도 잡이 두 개 생기지 않는다.
    again = await client.post(
        f"/projects/{pid}/manuscripts/{mid}/file/complete", headers=user["headers"]
    )
    assert again.json()["data"]["extraction_job_id"] == data["extraction_job_id"]


async def test_upload_complete_requires_an_issued_url(client: AsyncClient) -> None:
    user, pid = await _project(client, "ms8@example.com")
    ms = await _manuscript(client, user, pid, source_type="upload")
    res = await client.post(
        f"/projects/{pid}/manuscripts/{ms['manuscript_id']}/file/complete",
        headers=user["headers"],
    )
    assert res.status_code == 400
    assert code(res) == "INVALID_INPUT"


async def test_chapter_project_id_must_match_its_manuscript(
    client: AsyncClient, db: AsyncSession
) -> None:
    """서비스가 아니라 **DB** 가 막는다 — 복합 FK (manuscript_id, project_id)."""
    from sqlalchemy.exc import IntegrityError

    from app.content.manuscripts.models import Chapter

    user, pid = await _project(client, "ch3@example.com")
    ms = await _manuscript(client, user, pid)

    db.add(
        Chapter(
            project_id=UUID("00000000-0000-0000-0000-000000000000"),
            manuscript_id=UUID(ms["manuscript_id"]),
            chapter_no=1,
            content="",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
