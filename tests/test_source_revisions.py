from io import BytesIO
from uuid import UUID

from pypdf import PdfReader
from sqlalchemy import update

from app.content.manuscripts.pdf_export import to_pdf
from app.db.session import SessionFactory
from app.insight.fts.models import Foreshadowing
from tests.test_relationships import body, create, setup
from tests.test_writer_workspace import review, viewer


async def test_source_changes_confirm_restore_and_permissions(client):
    s = await setup(client)
    relation = await create(client, s)
    rid = relation["relationship_id"]
    fs = (
        await client.post(
            s["base"] + "/foreshadowings",
            headers=s["h"],
            json={"title": "문 앞의 약속", "setup_chapter_id": s["chapters"][0][1]},
        )
    ).json()["data"]
    fp = s["base"] + "/foreshadowings/" + fs["foreshadowing_id"]
    rp = s["base"] + "/relationships/" + rid
    cp = rp + "/history/" + s["chapters"][0][1] + "/source-confirmation"

    async def statuses():
        f = (await client.get(fp, headers=s["h"])).json()["data"]
        r = (await client.get(rp, headers=s["h"])).json()["data"]["history"][0]
        return f["source_review"], r["source_review"]

    initial = await statuses()
    assert all(r["status"] == "current" for r in initial)
    # Another manuscript in the same project is irrelevant.
    await client.patch(
        s["base"] + "/chapters/" + s["chapters"][1][3],
        headers=s["h"],
        json={"content": "별도 원고"},
    )
    assert await statuses() == initial
    await client.patch(
        s["base"] + "/chapters/" + s["chapters"][0][3],
        headers=s["h"],
        json={"content": "회수가 사라짐"},
    )
    changed = await statuses()
    assert all(r["status"] == "changed" and r["changed_chapters"] == [3] for r in changed)
    # A stale page cannot confirm unseen changes; reader cannot confirm at all.
    assert (
        await client.post(
            fp + "/source-confirmation",
            headers=s["h"],
            json={"revision_token": initial[0]["revision_token"]},
        )
    ).status_code == 400
    vh = await viewer(client, s)
    for path, state in zip([fp + "/source-confirmation", cp], changed, strict=True):
        payload = {"revision_token": state["revision_token"]}
        assert (await client.post(path, headers=vh, json=payload)).status_code == 403
        response = await client.post(path, headers=s["h"], json=payload)
        assert response.status_code == 200, response.text
    assert all(r["status"] == "current" for r in await statuses())
    # A later edit warns again. Source restoration compares actual content, not timestamps.
    await client.patch(
        s["base"] + "/chapters/" + s["chapters"][0][2], headers=s["h"], json={"title": "바뀐 제목"}
    )
    assert all(r["changed_chapters"] == [2] for r in await statuses())
    await client.patch(
        s["base"] + "/chapters/" + s["chapters"][0][2], headers=s["h"], json={"title": ""}
    )
    assert all(r["status"] == "current" for r in await statuses())
    added = (
        await client.post(
            s["base"] + "/chapters",
            headers=s["h"],
            json={"manuscript_id": s["ms"][0], "chapter_no": 4, "content": "새 장"},
        )
    ).json()["data"]["chapter_id"]
    assert all(r["changed_chapters"] == [4] for r in await statuses())
    await client.delete(s["base"] + "/chapters/" + added, headers=s["h"])
    assert all(r["status"] == "current" for r in await statuses())
    # Unknown legacy provenance must remain explicit until the writer confirms it.
    async with SessionFactory() as session:
        await session.execute(
            update(Foreshadowing)
            .where(Foreshadowing.id == UUID(fs["foreshadowing_id"]))
            .values(source_snapshot=None)
        )
        await session.commit()
    unknown = (await statuses())[0]
    assert unknown["status"] == "unverified"
    assert (
        await client.post(
            fp + "/source-confirmation",
            headers=s["h"],
            json={"revision_token": unknown["revision_token"]},
        )
    ).status_code == 200
    assert (await statuses())[0]["status"] == "current"
    saved = (await client.get(rp, headers=s["h"])).json()["data"]["history"][0]
    assert saved["state"] == "신뢰" and saved["event_title"] == "함께 기록을 찾았다"


async def test_candidate_replacement_requires_compared_revision_and_is_idempotent(client):
    s = await setup(client)
    path = await review(client, s, ["relationship", "relationship"])
    relation = await create(client, s)
    rid = relation["relationship_id"]
    rp = s["base"] + "/relationships/" + rid
    token = relation["history"][0]["source_review"]["revision_token"]
    payload = {"relationship": body(s), "relationship_id": rid, "replace_existing": True}
    payload["relationship"]["initial_history"]["state"] = "갈등"
    assert (await client.post(path + "/0", headers=s["h"], json=payload)).status_code == 400
    payload["expected_history_token"] = token
    # Concurrent change of the saved note must not be overwritten.
    edited = {**body(s)["initial_history"], "event_title": "다른 작가가 보충한 설명"}
    assert (
        await client.post(rp + "/history?overwrite=true", headers=s["h"], json=edited)
    ).status_code == 201
    assert (await client.post(path + "/0", headers=s["h"], json=payload)).status_code == 400
    current = (await client.get(rp, headers=s["h"])).json()["data"]["history"][0]
    assert current["event_title"] == edited["event_title"]
    payload["expected_history_token"] = current["source_review"]["revision_token"]
    accepted = await client.post(path + "/0", headers=s["h"], json=payload)
    assert accepted.status_code == 200, accepted.text
    assert (await client.post(path + "/0", headers=s["h"], json=payload)).json() == accepted.json()
    history = (await client.get(rp, headers=s["h"])).json()["data"]["history"]
    assert len(history) == 1 and history[0]["state"] == "갈등"


def test_pdf_preserves_indentation_tabs_internal_spaces_markup_and_long_pages():
    source = (
        "기준 줄\n    네 칸\n\t탭 시작\n가\t나\n중간    공백\n\u3000전각 공백\n<주문> & 약속\n\n"
        + "긴 문단입니다. " * 1000
    )
    reader = PdfReader(BytesIO(to_pdf([(None, source)])))
    text = "\n".join(p.extract_text() for p in reader.pages)
    for expected in [
        "    네 칸",
        "        탭 시작",
        "가       나",
        "중간    공백",
        "\u3000전각 공백",
        "<주문> & 약속",
    ]:
        assert expected in text
    assert len(reader.pages) > 1
    assert "긴 문단입니다." in reader.pages[-1].extract_text()


async def test_migration_recovers_historical_adoption_source_not_current_manuscript(client):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    s = await setup(client)
    path = await review(client, s, ["foreshadowing", "relationship"])
    made = (
        await client.post(
            path + "/0",
            headers=s["h"],
            json={
                "foreshadowing": {
                    "title": "옛 검토에서 채택",
                    "setup_chapter_id": s["chapters"][0][1],
                }
            },
        )
    ).json()["data"]["target_id"]
    rid = (await client.post(path + "/1", headers=s["h"], json={"relationship": body(s)})).json()[
        "data"
    ]["target_id"]
    await client.patch(
        s["base"] + "/chapters/" + s["chapters"][0][3],
        headers=s["h"],
        json={"content": "배포 전에 이미 바뀐 결말"},
    )
    migration = importlib.import_module("app.db.alembic.versions.0019_record_source_revisions")

    def reinstall(connection):
        original = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.downgrade()
            migration.upgrade()
        finally:
            migration.op = original

    async with SessionFactory() as session:
        await (await session.connection()).run_sync(reinstall)
        await session.commit()
    for path in [s["base"] + "/foreshadowings/" + made, s["base"] + "/relationships/" + rid]:
        value = (await client.get(path, headers=s["h"])).json()["data"]
        state = value.get("source_review") or value["history"][0]["source_review"]
        assert state["status"] == "changed" and state["changed_chapters"] == [3]
