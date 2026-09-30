"""E2E 시나리오 — `uv run python -m scripts.verify.e2e [out.json]`.

전제: `scripts.verify.serve`(8001) 와 워커 4종이 떠 있다. dev DB 에 새 사용자를 만든다
(이메일에 실행 시각을 붙여 겹치지 않게 한다). 기대와 다른 상태 코드가 나오면 그 단계에서 멈춘다.

받은 응답은 전부 out.json 에 남긴다 — openapi 대조(scripts.verify.contract)의 입력이다.
"""

import json
import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8001/v1"
RUN = str(int(time.time()))
log: list[dict[str, Any]] = []


class Blocked(Exception):
    pass


def call(
    name: str,
    client: httpx.Client,
    method: str,
    path: str,
    expect: int,
    token: str | None = None,
    **kw: Any,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    res = client.request(method, BASE + path, headers=headers, **kw)
    body = res.json() if res.content else None
    log.append(
        {"step": name, "method": method, "path": path, "status": res.status_code, "body": body}
    )
    print(f"\n### {name}\n{method} {path} → {res.status_code}")
    print(json.dumps(body, ensure_ascii=False, indent=1)[:1500])
    if res.status_code != expect:
        raise Blocked(f"{name}: 기대 {expect}, 실제 {res.status_code}")
    return body


def signup(client: httpx.Client, who: str) -> tuple[str, str]:
    email = f"{who}-{RUN}@example.com"
    body = call(
        f"Google 인증({who})",
        client,
        "POST",
        "/auth/oauth/google",
        200,
        json={"oauth_code": f"sub-{email}|{email}"},
    )
    body = call(
        f"가입({who})",
        client,
        "POST",
        "/auth/signup",
        201,
        json={
            "signup_ticket": body["data"]["signup_ticket"],
            "username": f"{who}{RUN}",
            "role": "writer",
        },
    )
    return body["data"]["tokens"]["access_token"], email


def wait_for(what: str, fn: Any, timeout: float = 30) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(1)
    raise Blocked(f"{what}: {timeout}초 안에 오지 않았다")


def run(client: httpx.Client) -> None:
    a, _ = signup(client, "alice")
    b, b_email = signup(client, "bob")

    call("내 정보", client, "GET", "/users/me", 200, a)
    call("개인 프로젝트 생성", client, "POST", "/projects", 201, a, json={"title": "개인 프로젝트"})
    team = call("팀 생성", client, "POST", "/teams", 201, a, json={"name": f"팀{RUN}"})["data"]
    team_id = team["team_id"]
    project = call(
        "팀 프로젝트 생성",
        client,
        "POST",
        "/projects",
        201,
        a,
        json={"title": "팀 프로젝트", "owner_type": "team", "team_id": team_id},
    )["data"]
    project_id = project["project_id"]

    inv = call(
        "팀 초대",
        client,
        "POST",
        f"/teams/{team_id}/invitations",
        201,
        a,
        json={"invited_email": b_email, "role": "member"},
    )["data"]
    call(
        "초대 수락(토큰)",
        client,
        "POST",
        f"/teams/{team_id}/invitations/{inv['invitation_id']}/accept",
        200,
        b,
        json={"token": inv["token"]},
    )
    members = call("팀 멤버 목록", client, "GET", f"/teams/{team_id}/members", 200, a)
    names = [m.get("username") for m in members["data"]]
    print("username 들:", names)
    if f"bob{RUN}" not in names:
        raise Blocked("멤버 목록에 bob 의 username 이 없다")
    pm = call("프로젝트 멤버 목록", client, "GET", f"/projects/{project_id}/members", 200, b)
    sources = {m["username"]: (m["role"], m["source"]) for m in pm["data"]}
    print("role · source:", sources)
    if sources.get(f"bob{RUN}") != ("editor", "team"):
        raise Blocked("팀 프로젝트 멤버 목록에 팀원 bob 이 editor · team 으로 없다")

    ms = call(
        "원고 생성(upload)",
        client,
        "POST",
        f"/projects/{project_id}/manuscripts",
        201,
        b,
        json={"title": "원고", "source_type": "upload"},
    )["data"]
    ms_path = f"/projects/{project_id}/manuscripts/{ms['manuscript_id']}"
    up = call(
        "presigned 발급", client, "POST", ms_path + "/file", 202, b, json={"file_format": "txt"}
    )["data"]

    text = "제1장 시작\n그날 밤, 비가 내렸다.\n"
    put = httpx.put(up["upload_url"], content=text.encode(), headers={"Content-Type": "text/plain"})
    log.append({"step": "S3 PUT", "status": put.status_code, "body": put.text[:500]})
    print(f"\n### S3 PUT → {put.status_code} {put.text[:300]}")
    if put.status_code != 200:
        raise Blocked(f"S3 PUT 실패 {put.status_code}")

    call("complete 콜백", client, "POST", ms_path + "/file/complete", 200, b)

    def ready() -> Any:
        res = client.get(BASE + ms_path, headers={"Authorization": f"Bearer {b}"})
        data = res.json()["data"]
        return data if data["status"] in ("ready", "failed") else None

    wait_for("추출 완료", ready)
    done = call("원고 조회(추출 후)", client, "GET", ms_path, 200, b)["data"]
    if done["status"] != "ready":
        raise Blocked(f"추출 실패: status={done['status']}")
    snaps = call("편집 이력(업로드 직후)", client, "GET", ms_path + "/versions", 200, b)["data"]
    if [v["source"] for v in snaps] != ["upload"]:
        raise Blocked("추출 완료가 업로드 스냅샷을 남기지 않았다")
    edit = {"content": done["content"] + "\n덧붙인 문장."}
    call("원고 본문 수정", client, "PATCH", ms_path, 200, b, json=edit)
    snaps = call("편집 이력(수정 후)", client, "GET", ms_path + "/versions", 200, b)["data"]
    if [v["source"] for v in snaps] != ["editor", "upload"]:
        raise Blocked("편집기 저장이 새 스냅샷이 되지 않았다")

    call(
        "챕터 생성",
        client,
        "POST",
        f"/projects/{project_id}/chapters",
        201,
        b,
        json={"manuscript_id": ms["manuscript_id"], "chapter_no": 1, "title": "1장"},
    )
    call("챕터 목록", client, "GET", f"/projects/{project_id}/chapters", 200, b)

    def invite_noti() -> Any:
        res = client.get(BASE + "/notifications", headers={"Authorization": f"Bearer {b}"})
        return [n for n in res.json()["data"] if n.get("type") == "team_invite"]

    wait_for("초대 알림", invite_noti)
    call("알림 목록(bob)", client, "GET", "/notifications", 200, b)

    characters(client, b, project_id)
    world_rules(client, b, project_id)
    foreshadowings(client, b, project_id, ms["manuscript_id"])
    nl_extraction(client, b, project_id)
    rule_extraction(client, b, project_id, ms["manuscript_id"])
    ask(client, b, project_id, ms["manuscript_id"])
    conflicts(client, b, project_id, ms["manuscript_id"])
    structure(client, b, project_id, ms["manuscript_id"])
    settings = call("알림 설정", client, "GET", "/users/me/notification-settings", 200, b)
    if len(settings["data"]) != 5:
        raise Blocked("알림 설정이 유형 5개가 아니다")
    off = {"type": "mention", "email_enabled": False}
    call("알림 설정 변경", client, "PATCH", "/users/me/notification-settings", 200, b, json=off)


def characters(client: httpx.Client, t: str, pid: str) -> None:
    """ASS 수동 경로 — 빈 초안 → 항목 편집 → 확정 → 중복(409) → merge → 캐릭터 CRUD."""
    drafts = f"/projects/{pid}/character-drafts"

    def draft(name: str | None) -> str:
        body = {"character_name": name} if name else {}
        return str(
            call(f"초안 생성({name})", client, "POST", drafts, 201, t, json=body)["data"][
                "draft_id"
            ]
        )

    def item(did: str, field: str, value: str) -> str:
        res = call(
            f"항목 추가({value})",
            client,
            "POST",
            f"{drafts}/{did}/items",
            201,
            t,
            json={"field": field, "value": value},
        )
        return str(res["data"]["item_id"])

    d1 = draft(None)
    tag = item(d1, "personality_tags", "신중함")
    item(d1, "core_values", "약속 중시")
    gone = item(d1, "emotion_keywords", "분노")
    call(
        "항목 수정", client, "PATCH", f"{drafts}/{d1}/items/{tag}", 200, t, json={"value": "집요함"}
    )
    call("항목 삭제", client, "DELETE", f"{drafts}/{d1}/items/{gone}", 204, t)
    call("이름 없이 확정", client, "POST", f"{drafts}/{d1}/confirm", 400, t, json={})
    call(
        "초안 이름 수정", client, "PATCH", f"{drafts}/{d1}", 200, t, json={"character_name": "윤서"}
    )
    hist = call(
        "항목 편집 이력", client, "GET", f"{drafts}/{d1}/edit-history?item_id={tag}", 200, t
    )
    if [e["action"] for e in hist["data"]] != ["added", "modified"]:
        raise Blocked("항목 하나의 이력이 추가·수정 2건이 아니다")
    call("초안 목록", client, "GET", drafts, 200, t)
    char = call("초안 확정", client, "POST", f"{drafts}/{d1}/confirm", 201, t, json={})["data"]
    cid = char["character_id"]
    if [a["origin"] for a in char["personality_tags"]] != ["user_added"]:
        raise Blocked("확정 캐릭터에 origin 이 승계되지 않았다")
    call(
        "확정된 초안 수정", client, "PATCH", f"{drafts}/{d1}", 409, t, json={"character_name": "x"}
    )

    d2 = draft("윤서")
    item(d2, "emotion_keywords", "불안")
    dup = call("같은 이름 확정", client, "POST", f"{drafts}/{d2}/confirm", 409, t, json={})
    if dup["error"]["details"].get("candidate_character_id") != cid:
        raise Blocked("중복 후보 id 가 기존 캐릭터가 아니다")
    merge = {"resolution": "merge", "merge_target_character_id": cid}
    merged = call("merge 확정", client, "POST", f"{drafts}/{d2}/confirm", 200, t, json=merge)
    if [a["value"] for a in merged["data"]["emotion_keywords"]] != ["불안"]:
        raise Blocked("merge 가 항목을 합치지 않았다")
    twin_draft = draft("윤서")
    new = {"resolution": "create_new"}
    twin = call(
        "동명 새 인물로 확정", client, "POST", f"{drafts}/{twin_draft}/confirm", 201, t, json=new
    )
    if twin["data"]["character_id"] == cid:
        raise Blocked("create_new 가 기존 캐릭터를 돌려줬다")

    d3 = draft("임시")
    call("초안 폐기", client, "POST", f"{drafts}/{d3}/discard", 200, t)
    call("폐기된 초안 확정", client, "POST", f"{drafts}/{d3}/confirm", 409, t, json={})

    chars = f"/projects/{pid}/characters"
    call("캐릭터 목록", client, "GET", chars, 200, t)
    call("캐릭터 조회", client, "GET", f"{chars}/{cid}", 200, t)
    call("캐릭터 이름 수정", client, "PATCH", f"{chars}/{cid}", 200, t, json={"name": "윤서하"})
    call("캐릭터 편집 이력", client, "GET", f"{chars}/{cid}/edit-history", 200, t)
    d4 = draft("지울 인물")
    doomed = call("초안 확정(삭제용)", client, "POST", f"{drafts}/{d4}/confirm", 201, t, json={})
    did = doomed["data"]["character_id"]
    call("캐릭터 삭제", client, "DELETE", f"{chars}/{did}", 204, t)
    call("삭제된 캐릭터", client, "GET", f"{chars}/{did}", 404, t)


def world_rules(client: httpx.Client, t: str, pid: str) -> None:
    """REX 직접 입력 — 추가 · 목록 · 수정 · 삭제."""
    rules = f"/projects/{pid}/world-rules"
    body = {
        "title": "붉은 문",
        "description": "붉은 문은 비가 그친 뒤에만 열린다",
        "violation_keywords": ["비 오는 중에 열림", "맑은 날 개방"],
    }
    rule = call("규칙 추가", client, "POST", rules, 201, t, json=body)["data"]
    path = f"{rules}/{rule['rule_id']}"
    call("규칙 목록", client, "GET", rules, 200, t)
    kw = {"violation_keywords": ["문이 저절로 열림"]}
    edited = call("규칙 키워드 수정", client, "PATCH", path, 200, t, json=kw)["data"]
    if edited["violation_keywords"] != ["문이 저절로 열림"] or edited["title"] != "붉은 문":
        raise Blocked("규칙 PATCH 가 키워드만 바꾸지 않았다")
    call("규칙 삭제", client, "DELETE", path, 204, t)
    call("삭제된 규칙 수정", client, "PATCH", path, 404, t, json={"title": "x"})


def foreshadowings(client: httpx.Client, t: str, pid: str, ms: str) -> None:
    """FTS — 설치·연결·회수 · 미회수 · 타임라인 · 설치 챕터 삭제 → orphaned(실제 워커 경유)."""
    ch = {}
    for no in (11, 12, 13):
        body = {"manuscript_id": ms, "chapter_no": no, "title": f"{no}장"}
        ch[no] = call(
            f"챕터 생성({no})", client, "POST", f"/projects/{pid}/chapters", 201, t, json=body
        )["data"]["chapter_id"]
    base = f"/projects/{pid}/foreshadowings"
    body = {"title": "붉은 흉터", "setup_chapter_id": ch[11]}
    f = call("복선 생성", client, "POST", base, 201, t, json=body)["data"]
    path = f"{base}/{f['foreshadowing_id']}"
    call(
        "연결 챕터 추가",
        client,
        "POST",
        f"{path}/linked-chapters",
        201,
        t,
        json={"chapter_id": ch[12]},
    )
    early = {"payoff_chapter_id": ch[11]}
    call("회수 지정(설치와 같은 장)", client, "PUT", f"{path}/payoff", 200, t, json=early)
    call("회수 취소", client, "DELETE", f"{path}/payoff", 200, t)
    call("회수 취소(이미 미회수)", client, "DELETE", f"{path}/payoff", 409, t)
    call("미회수 목록", client, "GET", f"{base}/unresolved?current_chapter=13", 200, t)
    call("미회수 안내", client, "GET", f"{base}/unresolved/advisories?current_chapter=13", 200, t)
    call("복선 타임라인", client, "GET", f"/projects/{pid}/foreshadowing-timeline", 200, t)
    call("복선 목록", client, "GET", base, 200, t)
    refs = call(
        "챕터의 복선", client, "GET", f"/projects/{pid}/chapters/{ch[11]}/foreshadowings", 200, t
    )
    if [r["role"] for r in refs["data"]] != ["setup"]:
        raise Blocked("설치 챕터 역참조가 setup 이 아니다")
    call("설치 챕터 삭제", client, "DELETE", f"/projects/{pid}/chapters/{ch[11]}", 204, t)

    def orphaned() -> Any:
        res = client.get(BASE + path, headers={"Authorization": f"Bearer {t}"})
        return res.json()["data"] if res.json()["data"]["status"] == "orphaned" else None

    wait_for("orphaned 전이(outbox → 워커)", orphaned)
    call("orphaned 복선", client, "GET", path, 200, t)
    fix = {"setup_chapter_id": ch[12]}
    fixed = call("설치 챕터 재지정", client, "PATCH", path, 200, t, json=fix)["data"]
    if fixed["status"] != "unresolved":
        raise Blocked("재지정 뒤에도 orphaned 다")


def nl_extraction(client: httpx.Client, t: str, pid: str) -> None:
    """NLCD — 제출 → ai 워커(USE_FAKE_LLM=1 이면 빈 추출) → 폴링 → forward → ASS 초안."""
    base = f"/projects/{pid}/nl-extractions"
    body = {"source_text": "윤서는 신중하지만 끝까지 물고 늘어졌다."}
    eid = call("자연어 추출 제출", client, "POST", base, 202, t, json=body)["data"]["extraction_id"]
    call("전달(완료 전)", client, "POST", f"{base}/{eid}/forward", 409, t, json={})

    def done() -> Any:
        res = client.get(f"{BASE}{base}/{eid}", headers={"Authorization": f"Bearer {t}"})
        return res.json() if res.json()["data"]["status"] != "analyzing" else None

    wait_for("추출 완료(ai 워커)", done, timeout=60)
    got = call("추출 결과", client, "GET", f"{base}/{eid}", 200, t)
    if got["data"]["status"] != "completed":
        raise Blocked(f"추출 실패: {got.get('error')}")
    call("추출 이력", client, "GET", base, 200, t)
    fwd = call("구조화로 전달", client, "POST", f"{base}/{eid}/forward", 201, t, json={})["data"]
    draft = call(
        "전달된 초안",
        client,
        "GET",
        f"/projects/{pid}/character-drafts/{fwd['forwarded_draft_id']}",
        200,
        t,
    )
    if draft["data"]["source_job_id"] != eid:
        raise Blocked("초안이 추출 잡에 연결되지 않았다")
    call("재전달", client, "POST", f"{base}/{eid}/forward", 409, t, json={})


def rule_extraction(client: httpx.Client, t: str, pid: str, ms: str) -> None:
    """REX AI 추출 — 요청 → ai 워커 → 폴링 → 확정(후보가 있으면)."""
    base = f"/projects/{pid}/manuscripts/{ms}/rule-extractions"
    eid = call("규칙 추출 요청", client, "POST", base, 202, t)["data"]["extraction_id"]

    def done() -> Any:
        res = client.get(f"{BASE}{base}/{eid}", headers={"Authorization": f"Bearer {t}"})
        return res.json() if res.json()["data"]["status"] in ("completed", "failed") else None

    wait_for("규칙 추출 완료(ai 워커)", done, timeout=60)
    got = call("규칙 추출 결과", client, "GET", f"{base}/{eid}", 200, t)
    if got["data"]["status"] != "completed":
        raise Blocked(f"규칙 추출 실패: {got.get('error')}")
    picked = {"selected_indices": [c["index"] for c in got["data"]["extracted_rules"]][:1]}
    call("추출 규칙 확정", client, "POST", f"{base}/{eid}/confirm", 201, t, json=picked)


def ask(client: httpx.Client, t: str, pid: str, ms: str) -> None:
    """AIQ — 질문 → ai 워커 → 답변 폴링 → 후속 질문."""
    base = f"/projects/{pid}/manuscripts/{ms}/qa-threads"
    body = {"question": "비는 언제 그쳤나요?"}
    made = call("질문 스레드 생성", client, "POST", base, 201, t, json=body)["data"]
    tid, mid = made["thread"]["thread_id"], made["messages"][1]["message_id"]
    poll = f"{base}/{tid}/messages/{mid}"

    def answered() -> Any:
        res = client.get(BASE + poll, headers={"Authorization": f"Bearer {t}"})
        return res.json() if res.json()["data"]["status"] != "pending" else None

    wait_for("답변(ai 워커)", answered, timeout=60)
    got = call("답변 조회", client, "GET", poll, 200, t)
    if got["data"]["status"] != "completed":
        raise Blocked(f"답변 실패: {got['data']['error']}")
    call("후속 질문", client, "POST", f"{base}/{tid}/messages", 201, t, json={"content": "왜요?"})
    call("스레드 목록", client, "GET", base, 200, t)
    call("완료된 답변 재시도", client, "POST", f"{poll}/retry", 409, t)


def conflicts(client: httpx.Client, t: str, pid: str, ms: str) -> None:
    """SCDS — 사건 저장(룰 검출 동기) → ai 워커 분석 → 검사 폴링 → 충돌 목록."""
    body = {"manuscript_id": ms, "chapter_no": 20, "title": "20장"}
    chapter = call("챕터 생성(20)", client, "POST", f"/projects/{pid}/chapters", 201, t, json=body)[
        "data"
    ]["chapter_id"]
    drafts = f"/projects/{pid}/character-drafts"
    did = call(
        "초안(사건 인물)", client, "POST", drafts, 201, t, json={"character_name": "사건 인물"}
    )["data"]["draft_id"]
    cid = call("확정(사건 인물)", client, "POST", f"{drafts}/{did}/confirm", 201, t, json={})[
        "data"
    ]["character_id"]
    rule = {
        "title": "야간 통행",
        "description": "밤에는 성문을 열 수 없다",
        "violation_keywords": ["한밤에 성문"],
    }
    call("규칙 추가(SCDS)", client, "POST", f"/projects/{pid}/world-rules", 201, t, json=rule)
    events = f"/projects/{pid}/chapters/{chapter}/events"
    quiet = call(
        "사건 저장(후보 없음)",
        client,
        "POST",
        events,
        201,
        t,
        json={"character_ids": [cid], "content": "낮에 문을 열었다."},
    )
    if quiet["data"]["conflict_check"]["status"] != "skipped":
        raise Blocked("후보 없는 사건이 skipped 가 아니다")
    loud = {"character_ids": [cid], "content": "그는 한밤에 성문을 열었다."}
    made = call("사건 저장(후보 있음)", client, "POST", events, 201, t, json=loud)["data"]
    check = made["conflict_check"]
    if check["status"] != "queued":
        raise Blocked("후보가 있는데 queued 가 아니다")
    path = f"/projects/{pid}/conflict-checks/{check['check_id']}"

    def analyzed() -> Any:
        res = client.get(BASE + path, headers={"Authorization": f"Bearer {t}"})
        return res.json() if res.json()["data"]["status"] in ("completed", "failed") else None

    wait_for("AI 분석(ai 워커)", analyzed, timeout=60)
    call("충돌 검사 결과", client, "GET", path, 200, t)
    call("충돌 목록", client, "GET", f"/projects/{pid}/conflicts", 200, t)
    call("충돌 이력", client, "GET", f"/projects/{pid}/conflicts/history", 200, t)


def structure(client: httpx.Client, t: str, pid: str, ms: str) -> None:
    """SSM — 분석 요청 → ai 워커(하트비트) → 폴링 → 구조 지도."""
    base = f"/projects/{pid}/manuscripts/{ms}"
    aid = call("구조 분석 요청", client, "POST", f"{base}/structure-analyses", 202, t)["data"][
        "analysis_id"
    ]
    poll = f"{base}/structure-analyses/{aid}"

    def done() -> Any:
        res = client.get(BASE + poll, headers={"Authorization": f"Bearer {t}"})
        return res.json() if res.json()["data"]["status"] in ("completed", "failed") else None

    wait_for("구조 분석(ai 워커)", done, timeout=60)
    got = call("구조 분석 결과", client, "GET", poll, 200, t)
    if got["data"]["status"] != "completed":
        raise Blocked(f"구조 분석 실패: {got.get('error')}")
    call("구조 지도", client, "GET", f"{base}/structure-map", 200, t)
    call("완료된 분석 재시도", client, "POST", f"{poll}/retry", 409, t)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "e2e_responses.json"
    try:
        with httpx.Client(timeout=15) as client:
            run(client)
        print("\n=== 전 구간 통과 ===")
    except Blocked as exc:
        print(f"\n=== 막힘: {exc} ===")
    finally:
        with open(out, "w") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
