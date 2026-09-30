"""원고 편집 이력 — 스냅샷 정책(`app.content.manuscripts.versions`)과 조회 API."""

from datetime import datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.manuscripts import versions
from app.content.manuscripts.extraction import FileExtractor
from app.content.manuscripts.job_handler import handle
from app.content.manuscripts.models import Manuscript
from app.db.session import SessionFactory
from tests.conftest import FakeStorage, code, signup
from tests.test_extraction import _queued_extraction


async def _editor(client: AsyncClient, email: str) -> tuple[dict, str]:
    user = await signup(client, email)
    pid = (
        await client.post(
            "/projects", json={"title": "P", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    mid = (
        await client.post(
            f"/projects/{pid}/manuscripts", json={"title": "원고"}, headers=user["headers"]
        )
    ).json()["data"]["manuscript_id"]
    return user, f"/projects/{pid}/manuscripts/{mid}"


async def _versions(client: AsyncClient, user: dict, path: str) -> list[dict]:
    res = await client.get(f"{path}/versions", headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def test_small_saves_in_a_window_become_one_snapshot(client: AsyncClient) -> None:
    user, path = await _editor(client, "mv1@example.com")
    h = user["headers"]
    assert await _versions(client, user, path) == []  # 빈 본문으로 만든 것은 스냅샷이 아니다

    for text in ("비가", "비가 그친 뒤에도", "비가 그친 뒤에도 골목에는"):
        await client.patch(path, json={"content": text}, headers=h)
    one = await _versions(client, user, path)
    assert [(v["version_no"], v["source"], v["content"], v["char_count"]) for v in one] == [
        (1, "editor", "비가 그친 뒤에도 골목에는", 14)
    ]
    assert one[0]["created_by"] == user["id"]

    await client.patch(path, json={"content": "비가 그친 뒤에도 골목에는"}, headers=h)  # 같다
    await client.patch(path, json={"title": "새 제목"}, headers=h)  # 제목만
    assert len(await _versions(client, user, path)) == 1

    big = "비가 그친 뒤에도 골목에는" + "붉은 빛이 남아 있었다. " * 100  # 1,000자 이상 변화
    await client.patch(path, json={"content": big}, headers=h)
    listed = await _versions(client, user, path)
    assert [v["version_no"] for v in listed] == [2, 1]  # 최신부터


async def test_window_expiry_starts_a_new_snapshot(client: AsyncClient, db: AsyncSession) -> None:
    user, path = await _editor(client, "mv2@example.com")
    await client.patch(path, json={"content": "첫 문장"}, headers=user["headers"])
    mid = UUID(path.rsplit("/", 1)[1])
    first = (await _versions(client, user, path))[0]

    manuscript = (await db.execute(select(Manuscript).where(Manuscript.id == mid))).scalar_one()
    manuscript.content = "첫 문장. 둘째 문장"
    later = datetime.fromisoformat(first["created_at"]) + versions.WINDOW + timedelta(seconds=1)
    inside = datetime.fromisoformat(first["created_at"]) + versions.WINDOW - timedelta(seconds=1)
    kept = await versions.snapshot(db, manuscript, "editor", None, now=inside)
    assert kept is not None and kept.version_no == 1  # 창 안 — 덮어쓴다
    manuscript.content = "첫 문장. 둘째 문장. 셋째"
    fresh = await versions.snapshot(db, manuscript, "editor", None, now=later)
    assert fresh is not None and fresh.version_no == 2  # 창 밖 — 새 스냅샷
    await db.commit()


async def test_upload_snapshot_is_never_overwritten(client: AsyncClient) -> None:
    ctx = await _queued_extraction(client, "mv3@example.com", "3차 원고 본문".encode(), "txt")
    assert await handle(SessionFactory, UUID(ctx["job_id"]), FakeStorage(), FileExtractor()) == (
        "completed"
    )
    user, path = ctx["user"], f"/projects/{ctx['pid']}/manuscripts/{ctx['mid']}"
    uploaded = await _versions(client, user, path)
    assert [(v["source"], v["content"], v["created_by"]) for v in uploaded] == [
        ("upload", "3차 원고 본문", user["id"])
    ]
    # 바로 이어진 작은 편집도 업로드 스냅샷을 덮어쓰지 않는다
    await client.patch(path, json={"content": "3차 원고 본문."}, headers=user["headers"])
    assert [v["source"] for v in await _versions(client, user, path)] == ["editor", "upload"]


async def test_versions_cursor_and_tenancy(client: AsyncClient) -> None:
    user, path = await _editor(client, "mv4@example.com")
    for n in range(3):
        await client.patch(path, json={"content": "가" * (1200 * (n + 1))}, headers=user["headers"])
    first = (await client.get(f"{path}/versions?limit=2", headers=user["headers"])).json()
    assert [v["version_no"] for v in first["data"]] == [3, 2]
    rest = await client.get(
        f"{path}/versions?limit=2&cursor={first['meta']['next_cursor']}", headers=user["headers"]
    )
    assert [v["version_no"] for v in rest.json()["data"]] == [1]

    other = await signup(client, "mv5@example.com")
    pid = path.split("/")[2]
    assert (await client.get(f"{path}/versions", headers=other["headers"])).status_code == 403
    mine = (
        await client.post(
            "/projects", json={"title": "Q", "owner_type": "personal"}, headers=user["headers"]
        )
    ).json()["data"]["project_id"]
    wrong = path.replace(pid, mine)
    assert code(await client.get(f"{wrong}/versions", headers=user["headers"])) == (
        "MANUSCRIPT_NOT_FOUND"
    )
