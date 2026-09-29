"""E2E 시나리오 — `uv run python -m scripts.verify.e2e [out.json]`.

전제: `scripts.verify.serve`(8001) 와 워커 3종이 떠 있다. dev DB 에 새 사용자를 만든다
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
