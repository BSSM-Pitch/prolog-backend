"""화면 개발용 기본 데이터 — `uv run python -m scripts.verify.seed`.

사용자 1 · 팀 1 · 프로젝트 2(개인 · 팀) · 원고 1 · 챕터 3 · 확정 캐릭터 2 · 검토 대기 초안 1
을 넣는다. 이미 있으면 아무것도
하지 않는다(여러 번 돌려도 쌓이지 않는다). 새로 시작하려면:

    uv run alembic downgrade base && uv run alembic upgrade head
    uv run python -m scripts.verify.seed

**로그인.** 사용자 행만 직접 넣는다(CLAUDE.md §5.4 — 로컬 가입 경로를 만들지 않는다).
Google 은 이 계정을 모르므로, Google 만 fake 인 `scripts.verify.serve`(8001)에 대고
`POST /v1/auth/oauth/google {"oauth_code": "seed-dev|dev@example.com"}` 로 토큰을 받는다.

**캐릭터.** AI 추출(NLCD) 경로가 아직 없어서 `ai_extracted` 항목은 행을 직접 넣는다(근거 문장 포함).
화면 "23 · 구조화 초안 검토" 의 윤서 초안이 그대로 들어간다. 확정은 서비스 함수를 부른다.

나머지는 서비스 함수를 그대로 부른다 — 팀 owner 등록 · 프로젝트 owner 등록 · 챕터 번호
제약이 실제 경로와 똑같이 적용된다.
"""

import asyncio
import sys
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authoring.ass import service as ass
from app.authoring.ass.models import CharacterDraftItem
from app.authoring.ass.schemas import CATEGORY_OF, ConfirmRequest, DraftCreate, ItemField
from app.content.manuscripts import service as manuscripts
from app.content.manuscripts.schemas import ChapterCreate, ManuscriptCreate, ManuscriptUpdate
from app.core.config import settings
from app.core.deps import CurrentUser
from app.db.session import SessionFactory
from app.platform_.auth.models import User
from app.platform_.projects import service as projects
from app.platform_.projects.schemas import ProjectCreate
from app.platform_.teams import service as teams
from app.platform_.teams.schemas import TeamCreate

SUB = "seed-dev"
EMAIL = "dev@example.com"
USERNAME = "dev"

CHAPTERS = [
    ("1화 · 물의 도시", "비가 그치자 운하의 물이 한 뼘쯤 올라와 있었다. 서하는 숫자를 셌다."),
    ("2화 · 등대지기", "등대는 삼 년째 꺼져 있었다. 그런데 오늘 밤, 누군가 불을 켰다."),
    ("3화 · 지도에 없는 섬", "여백에 연필로 그린 점 하나. 할머니는 그것을 '집'이라고 불렀다."),
]

AI, USER = "ai_extracted", "user_added"
# (이름, 확정 여부, [(field, value, origin, evidence)])
CHARACTERS: list[tuple[str, bool, list[tuple[ItemField, str, str, str | None]]]] = [
    (
        "서하",
        True,
        [
            ("personality_tags", "꼼꼼함", AI, "서하는 숫자를 셌다."),
            ("core_values", "기록을 남긴다", USER, None),
            ("influence_relations", "할머니", AI, "할머니는 그것을 '집'이라고 불렀다."),
            ("emotion_keywords", "불안", AI, "운하의 물이 한 뼘쯤 올라와 있었다."),
        ],
    ),
    (
        "등대지기",
        True,
        [
            ("personality_tags", "은둔", AI, "등대는 삼 년째 꺼져 있었다."),
            ("emotion_keywords", "그리움", USER, None),
        ],
    ),
    (
        "윤서",
        False,
        [
            ("personality_tags", "신중함", AI, "신중하지만"),
            ("personality_tags", "집요함", AI, "끝까지 물고 늘어졌다"),
            ("core_values", "약속 중시", AI, "약속은 지켜야 한다고 믿었다"),
            ("influence_relations", "재현", AI, "재현이 떠난 뒤로"),
            ("emotion_keywords", "불안", USER, None),
        ],
    ),
]


async def _characters(session: AsyncSession, project_id: UUID, user_id: UUID) -> list[str]:
    out = []
    for name, confirmed, items in CHARACTERS:
        draft = await ass.create_draft(
            session, project_id, DraftCreate(character_name=name), user_id
        )
        for field, value, origin, evidence in items:
            session.add(
                CharacterDraftItem(
                    project_id=project_id,
                    draft_id=draft.draft_id,
                    category=CATEGORY_OF[field],
                    value=value,
                    evidence=evidence,
                    origin=origin,
                    created_at=func.clock_timestamp(),  # 한 트랜잭션이라 now() 로는 순서가 없다
                )
            )
        await session.flush()
        if confirmed:
            character, _ = await ass.confirm(
                session, project_id, draft.draft_id, ConfirmRequest(), user_id
            )
            out.append(f"character   {character.character_id}  {name}")
        else:
            out.append(f"draft       {draft.draft_id}  {name} (검토 대기)")
    return out


def _refuse_non_local() -> None:
    host = urlsplit(settings.database_url.replace("+asyncpg", "")).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"로컬 DB 가 아니다(host={host}). seed 는 로컬 전용이다.")


async def main() -> None:
    _refuse_non_local()
    async with SessionFactory() as session:
        existing = (
            await session.execute(select(User).where(User.provider_user_id == SUB))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"이미 seed 되어 있다 (user_id={existing.id}). 아무것도 하지 않는다.")
            return

        user = User(
            email=EMAIL,
            auth_provider="google",
            provider_user_id=SUB,
            username=USERNAME,
            role="writer",
        )
        session.add(user)
        await session.flush()
        me = CurrentUser(id=user.id, email=EMAIL)

        team = await teams.create_team(
            session, me, TeamCreate(name="스토리포지 작가팀", description="화면 개발용 팀")
        )
        personal = await projects.create_project(
            session, me, ProjectCreate(title="개인 습작", description="혼자 쓰는 단편 모음")
        )
        shared = await projects.create_project(
            session,
            me,
            ProjectCreate(
                title="달의 뒷면",
                description="팀 연재 장편",
                owner_type="team",
                team_id=team.team_id,
            ),
        )

        manuscript = await manuscripts.create(
            session, shared.project_id, ManuscriptCreate(title="1부 초고", source_type="editor")
        )
        body = "\n\n".join(f"{title}\n{text}" for title, text in CHAPTERS)
        await manuscripts.update(
            session, shared.project_id, manuscript.manuscript_id, ManuscriptUpdate(content=body)
        )
        for no, (title, text) in enumerate(CHAPTERS, start=1):
            await manuscripts.create_chapter(
                session,
                shared.project_id,
                ChapterCreate(
                    manuscript_id=manuscript.manuscript_id, chapter_no=no, title=title, content=text
                ),
            )
        cast = await _characters(session, shared.project_id, user.id)
        await session.commit()

    print("seed 완료")
    print(f"  user        {user.id}  {USERNAME} <{EMAIL}>")
    print(f"  team        {team.team_id}  {team.name}")
    print(f"  project     {personal.project_id}  {personal.title} (personal)")
    print(f"  project     {shared.project_id}  {shared.title} (team)")
    print(f"  manuscript  {manuscript.manuscript_id}  챕터 {len(CHAPTERS)}")
    for line in cast:
        print(f"  {line}")
    print(f'  로그인: serve.py(8001) 에 oauth_code "{SUB}|{EMAIL}"')


if __name__ == "__main__":
    asyncio.run(main())
