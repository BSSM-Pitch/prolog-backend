# 관계 API 연동

RCV는 AI 호출 없이 사용자가 기록한 관계를 저장·조회한다. 인물 쌍은 프로젝트 단위, 변화 기록은 실제 `chapter_id` 단위다. 새 DB 테이블이나 마이그레이션은 필요하지 않는다.

## 제공 경로

모두 `/v1/projects/{projectId}` 아래다. 조회는 viewer 이상, 쓰기는 editor 이상이다.

- `GET/POST /relationships`: 커서 목록 / 관계와 최초 기록 생성
- `GET/DELETE /relationships/{relationshipId}`: 상세 / 모든 원고의 관계 이력 삭제
- `POST /relationships/{relationshipId}/history`: 상태 기록. 동일 장은 409 `DUPLICATE_CHAPTER_RECORD`, 명시적인 `overwrite=true`만 덮어쓴다.
- `DELETE /relationships/{relationshipId}/history/{chapterId}`: 해당 장 기록만 삭제
- `GET /relationships/{relationshipId}/snapshot?manuscript_id=…&chapter=N`: 해당 원고에서 N장 이하 마지막 기록
- `GET /relationship-mindmap?manuscript_id=…&chapter=N`: 같은 시점의 노드와 선

정확한 본문·응답은 `openapi.json`의 RCV 항목에 있다. 다른 프로젝트의 인물·장·원고 참조는 거부한다. 연결된 관계가 있는 인물 삭제는 409 `CHARACTER_HAS_DEPENDENT_RELATIONSHIPS`다.

## 초안 명세에서 구체화한 부분

- FTS와 같은 이유로 쓰기는 장 번호가 아닌 `chapter_id`를 받는다. 응답에는 `chapter`와 `chapter_id`, `manuscript_id`가 함께 온다. 목록·상세의 선택적 `manuscript_id`는 이력을 해당 원고로 제한한다.
- 같은 인물 쌍을 다른 원고에서 처음 기록할 때는 기존 관계에 새 원고의 기록을 추가한다. 이미 그 원고의 기록이 있으면 409 `DUPLICATE_RELATIONSHIP`이다. 역방향 쌍도 같은 관계로 취급한다.
- `event_title`은 프론트의 ‘관계 변화 메모’이며 기존 `note` 컬럼에 저장한다. 사건 ID 연결을 구현한 것으로 표시하지 않는다.
- 챕터 번호는 조회할 때 현재 Chapter에서 읽는다. 원고 간 같은 번호를 합치지 않고 미래의 상태를 앞선 장에 보여 주지 않는다.
- 초안의 미사용 history GET/PATCH, timeline, metrics, 캐릭터 역참조 경로는 이번 화면 연동 범위에 포함하지 않았다.

## 로컬 확인

일반 실행은 README와 같다. Google 키 없이 UI를 검증할 때만 `python -m scripts.verify.serve`를 사용한다(127.0.0.1:8001, Google만 대체 인증). 공개 배포에 사용하지 않는다. 원고·관계·복선은 실제 PostgreSQL에 저장된다.

프론트는 `VITE_API_MODE=real`, `VITE_BACKEND_URL=http://127.0.0.1:8001`, `VITE_DEV_GOOGLE_LOGIN=dev|dev@example.com`으로 연결한다. 실제 Google client ID는 비워 둔다. seed는 `python -m scripts.verify.seed`로 준비한다.

`tests/test_relationships.py`는 별도 `_test` DB에서 저장·재조회, 원고 분리, 장 삭제, 중복과 동시 요청, 권한, 타 프로젝트 참조, 페이지네이션을 확인한다. 실제 LLM 응답 검증은 별도 OpenRouter 키와 AI 워커가 필요하다.
