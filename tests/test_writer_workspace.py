import asyncio
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import func

from app.content.media.images import ImageStorage, image_storage, normalize
from app.core import errors
from app.db.session import SessionFactory
from app.jobs.models import Job
from app.main import app
from app.platform_.projects.models import ProjectMember
from tests.conftest import signup
from tests.test_relationships import body, setup


class MemoryImages(ImageStorage):
    def __init__(self):
        self.objects = {}

    def put(self, key, data):
        self.objects[key] = data

    def delete(self, key):
        self.objects.pop(key, None)

    def url(self, key):
        return f"https://images.example/{key}?temporary=yes"


@pytest.fixture
def images():
    store = MemoryImages()
    app.dependency_overrides[image_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(image_storage)


def png():
    out = BytesIO()
    Image.new("RGB", (80, 120), "#56836a").save(out, "PNG")
    return out.getvalue()


async def review(client, s, kinds):
    path = f"{s['base']}/manuscripts/{s['ms'][0]}/narrative-reviews"
    response = await client.post(path, headers=s["h"])
    jid = response.json()["data"]["check_id"]
    async with SessionFactory() as session:
        job = await session.get(Job, UUID(jid))
        job.status = "completed"
        job.result = {
            "tracks": [
                {
                    "kind": kind,
                    "title": "문 앞의 약속",
                    "status": "unresolved",
                    "explanation": "앞 장의 약속을 추적합니다.",
                    "evidence": [
                        {"chapter_id": s["chapters"][0][1], "chapter_no": 1, "quote": "1장의 원문"}
                    ],
                }
                for kind in kinds
            ],
            "findings": [],
        }
        await session.commit()
    return f"{s['base']}/conflict-checks/{jid}/adoptions"


async def viewer(client, s):
    user = await signup(client, "reader@example.com")
    async with SessionFactory() as session:
        session.add(
            ProjectMember(
                project_id=UUID(s["pid"]),
                user_id=UUID(user["id"]),
                role="viewer",
                joined_at=func.now(),
            )
        )
        await session.commit()
    return user["headers"]


async def test_images_persist_normalize_replace_and_enforce_ownership(client, images):
    s = await setup(client)
    path = f"{s['base']}/media/cover/{s['pid']}"
    made = await client.put(path, headers=s["h"], content=png())
    assert made.status_code == 200, made.text
    assert made.json()["data"]["width"] == 80
    first = next(iter(images.objects))
    assert Image.open(BytesIO(images.objects[first])).format == "WEBP"
    again = await client.put(path, headers=s["h"], content=png())
    assert again.status_code == 200 and len(images.objects) == 1 and first not in images.objects
    listed = await client.get(s["base"] + "/media", headers=s["h"])
    assert listed.json()["data"][0] == again.json()["data"]
    vh = await viewer(client, s)
    assert (await client.get(s["base"] + "/media", headers=vh)).status_code == 200
    assert (await client.put(path, headers=vh, content=png())).status_code == 403
    assert (await client.delete(path, headers=vh)).status_code == 403
    foreign = await setup(client, "other@example.com")
    bad = f"{s['base']}/media/portrait/{foreign['people'][0]}"
    assert (await client.put(bad, headers=s["h"], content=png())).status_code == 404
    assert (
        await client.put(path, headers=s["h"], content=b'<svg onload="alert(1)"/>')
    ).status_code == 400
    assert len(images.objects) == 1
    portrait = f"{s['base']}/media/portrait/{s['people'][0]}"
    assert (await client.put(portrait, headers=s["h"], content=png())).status_code == 200
    assert (await client.delete(path, headers=s["h"])).status_code == 204
    assert len(images.objects) == 1
    assert (await client.delete(portrait, headers=s["h"])).status_code == 204
    assert not images.objects


def test_image_limits_and_invalid_files():
    with pytest.raises(errors.InvalidInput):
        normalize(b"x" * (5 * 1024 * 1024 + 1))
    with pytest.raises(errors.InvalidInput):
        normalize(png()[:25])
    out = BytesIO()
    Image.new("RGB", (6000, 5000)).save(out, "PNG")
    with pytest.raises(errors.InvalidInput):
        normalize(out.getvalue())


async def test_adoption_atomic_idempotent_and_manuscript_scoped(client):
    s = await setup(client)
    path = await review(client, s, ["foreshadowing", "foreshadowing"])
    draft = {
        "foreshadowing": {
            "title": "확인한 복선",
            "description": "작가가 고친 설명",
            "setup_chapter_id": s["chapters"][0][2],
        },
        "payoff_chapter_id": s["chapters"][0][1],
    }
    failed = await client.post(path + "/0", headers=s["h"], json=draft)
    assert failed.status_code == 400, failed.text
    assert (await client.get(s["base"] + "/foreshadowings", headers=s["h"])).json()["data"] == []
    draft["payoff_chapter_id"] = s["chapters"][1][3]
    assert (await client.post(path + "/0", headers=s["h"], json=draft)).status_code == 404
    draft["payoff_chapter_id"] = s["chapters"][0][3]
    a, b = await asyncio.gather(
        *[client.post(path + "/0", headers=s["h"], json=draft) for _ in range(2)]
    )
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["data"] == b.json()["data"]
    rows = (await client.get(s["base"] + "/foreshadowings", headers=s["h"])).json()["data"]
    assert (
        len(rows) == 1
        and rows[0]["status"] == "resolved"
        and rows[0]["description"] == "작가가 고친 설명"
    )
    assert len((await client.get(path, headers=s["h"])).json()["data"]) == 1
    vh = await viewer(client, s)
    assert (await client.post(path + "/1", headers=vh, json=draft)).status_code == 403
    await client.patch(
        f"{s['base']}/chapters/{s['chapters'][0][1]}", headers=s["h"], json={"content": "변경됨"}
    )
    assert (await client.post(path + "/1", headers=s["h"], json=draft)).status_code == 400
    assert (await client.post(path + "/0", headers=s["h"], json=draft)).json()["data"] == a.json()[
        "data"
    ]


async def test_relationship_adoption_adds_history_without_overwriting(client):
    s = await setup(client)
    path = await review(client, s, ["relationship", "relationship", "relationship"])
    first = await client.post(path + "/0", headers=s["h"], json={"relationship": body(s)})
    assert first.status_code == 200, first.text
    rid = first.json()["data"]["target_id"]
    payload = {"relationship": body(s), "relationship_id": rid}
    duplicate = await client.post(path + "/1", headers=s["h"], json=payload)
    assert duplicate.status_code == 409, duplicate.text
    payload["relationship"] = body(s, chapter=3)
    assert (await client.post(path + "/1", headers=s["h"], json=payload)).status_code == 200
    payload["relationship"] = body(s, chapter=2, target=2)
    assert (await client.post(path + "/2", headers=s["h"], json=payload)).status_code == 400
    history = (await client.get(s["base"] + "/relationships/" + rid, headers=s["h"])).json()[
        "data"
    ]["history"]
    assert [h["chapter"] for h in history] == [1, 3]


async def test_export_formats_ranges_korean_and_literal_markup(client):
    s = await setup(client)
    text = "한글 원고와 漢字, <주문> & 약속. **원문 그대로**"
    await client.patch(
        f"{s['base']}/chapters/{s['chapters'][0][2]}", headers=s["h"], json={"content": text}
    )
    path = f"{s['base']}/manuscripts/{s['ms'][0]}/export"
    for fmt in ("pdf", "docx", "txt"):
        response = await client.get(
            path, headers=s["h"], params={"format": fmt, "from_chapter": 2, "to_chapter": 2}
        )
        assert response.status_code == 200, response.text
        assert "filename*=" in response.headers["content-disposition"]
        if fmt == "pdf":
            reader = PdfReader(BytesIO(response.content))
            extracted = "\n".join(page.extract_text() for page in reader.pages)
            assert text in extracted and "1장의 원문" not in extracted
            fonts = reader.pages[0]["/Resources"]["/Font"]
            assert any("/FontDescriptor" in font.get_object() for font in fonts.values())
        elif fmt == "txt":
            assert text in response.text and "1장의 원문" not in response.text
        else:
            import zipfile

            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                xml = archive.read("word/document.xml").decode()
                assert "&lt;주문&gt; &amp;" in xml and "1장의 원문" not in xml
    for query in (
        {"from_chapter": 3, "to_chapter": 1},
        {"from_chapter": 2},
        {"from_chapter": 1, "to_chapter": 99},
    ):
        assert (await client.get(path, headers=s["h"], params=query)).status_code == 400
