# 일정 기반 상태 제안 (Calendar Suggest) — 설계

- 날짜: 2026-10-10
- 브랜치: `feat/calendar-suggest`
- 상태: 설계 승인됨, 스펙 검토 대기

## 1. 목적

회사 일정이 있을 때마다 Admin·단축어로 직접 상태를 바꾸다 보니 번거롭고 가끔 잊는다.
일정 시작 직전(또는 지정한 시간대)에 **"'회의 중'으로 바꾸시겠어요?"** 를 받고, 한 번 눌러 바로 적용되게 한다.
일정이 끝나면 이전 상태로 자동으로 돌아간다.

성공 기준:

1. 키워드가 맞는 일정이 시작하기 약 5분 전에 Teams 메시지가 온다.
2. 메시지의 링크를 열고 **[바꾸기]** 한 번이면 패널이 바뀐다 (첫 로그인 이후).
3. 일정 종료 시각에, 그때까지 제안한 프리셋이 그대로 표시 중이면 [바꾸기] 직전의 프리셋으로 돌아간다.
4. 그 사이 직접 다른 프리셋으로 바꿨다면 아무것도 되돌리지 않는다.

## 2. 제약과 결정

| 항목 | 내용 |
|------|------|
| 캘린더 접근 | 회사 Outlook 은 폐쇄 환경 — 서버(GCP)는 일정을 읽을 수 없다. iPhone 기본 캘린더에도 동기화되지 않아 단축어도 못 읽는다. **Power Automate(회사 Microsoft 365)** 는 쓸 수 있다 |
| 동작 방식 | **제안 우선** (자동 갱신은 범위 밖). 제안을 받아들였을 때만 종료 시 자동 복귀 |
| 알림 채널 | Power Automate 의 "Send me a mobile notification" 은 모바일 앱 지원 종료(2026-08-31)로 더 이상 전달되지 않는다. **Teams Flow bot 메시지**를 쓰고, 막혀 있으면 Outlook 메일로 대체 |
| 커넥터 등급 | Office 365 Outlook·Microsoft Teams 커넥터는 Standard. HTTP 커넥터(Premium)는 쓰지 않는다 — 서버로 데이터를 "보내는" 쪽이 없고, 사람이 링크를 여는 방식 |
| 데이터 최소화 | 일정 제목은 회사(Teams 메시지) 안에만 둔다. 링크에는 **프리셋 이름과 종료 시각만** 싣는다 |
| 일정 → 프리셋 | Power Automate 에서 제목 키워드로 결정 (`점심`→점심 식사, `외근`→외근 중, 나머지→회의 중). 확인 화면에서 다른 프리셋을 고를 수 있다 |
| 종료 후 | 이전 상태로 자동 복귀 (설정 없음). 직접 바꿨으면 건드리지 않음 |
| 로그인 후 링크 복귀 | 포함. 로그인 전 열었던 `/suggest…` 로 로그인 후 돌아간다 (허용 목록 경로만) |
| 기본 브라우저 | iPhone·Mac 모두 Chrome. Teams 가 링크를 자체 브라우저로 열면 세션이 없어 처음 한 번 로그인이 필요하다 |
| 서버 런타임 | VM 은 **Python 3.10.12**. 3.11+ 전용 API(`datetime.fromisoformat` 의 `Z`·7자리 소수 초, `datetime.UTC`, `TaskGroup` 등)를 쓰지 않는다 |

범위 밖 (YAGNI): 완전 자동 갱신, 웹 푸시, 서버가 일정 데이터를 저장하는 것, 여러 사용자, 복귀 대상 직접 지정, 제안 이력.

## 3. 전체 흐름

```
Outlook 일정 (회사)
  │ "When an upcoming event is starting soon (V3)" — Look-Ahead 5분
  │ 제외: 종일 / showAs=free / 거절 / 제목이 Canceled:·취소됨: 으로 시작
  │ 제목 키워드 → 프리셋 이름
  ▼
Teams (Flow bot → 나와의 채팅)
  │ "10:00 주간회의 — '회의 중'으로 바꾸시겠어요?  [바꾸기 화면 열기]"
  │ 링크: https://<도메인>/suggest?preset=<이름>&until=<endWithTimeZone>
  ▼
GET /suggest  (세션 필요 · 화면만, 상태 변경 없음)
  │ 미로그인 → / (메인) → /login → Google → /auth/callback → 원래 /suggest… 로 복귀
  │ 제안 프리셋 미리보기 + [바꾸기] / [그대로 두기] / 다른 프리셋으로
  ▼ [바꾸기]
POST /api/suggestions/apply {preset_id, until}
  │ 활성화 (기존 activate — 전송 간격·중복 억제 그대로)
  │ 복귀 예약 {revert_to: 직전 프리셋, expected: 적용한 프리셋, at: until} → state.json
  ▼ until 시각
복귀: 그때 current_preset_id == expected 일 때만 revert_to 를 활성화
```

시간대 제안(흐름 B)은 트리거만 Recurrence(평일 11:55, Korea Standard Time)로 바꾼 같은 메시지다.

## 4. 서버 설계 (`server/main.py`)

### 4.1 상태

```python
# 제안 적용 후 종료 시각에 되돌릴 예약. 하나만 유지한다.
pending_revert: Optional[dict] = None   # {"revert_to": id, "expected": id, "at": datetime(UTC, aware)}
_revert_task: Optional[asyncio.Task] = None
```

- `persist_state()` 스냅샷에 `"pending_revert": {"revert_to", "expected", "at": ISO-8601 UTC}` (없으면 `null`) 추가.
- `restore_state()` 가 읽어 검증: 두 ID 모두 `PRESET_ID_RE`, `at` 은 아래 파서로 해석 가능해야 한다. 아니면 버린다.
- lifespan 이 복원 직후 타이머를 건다. `at` 이 이미 지났으면 곧바로 실행한다 (조건 검사는 동일). 종료 시 타이머 취소.

### 4.2 활성화와 원자성

`activate()` 의 본체를 `_activate_core(preset, force, expected_current=None) -> (result | None, previous_id)` 로 분리한다.
기존 `activate()` 는 이를 감싸 지금과 같은 dict 를 돌려준다 (기존 API 응답 형태 불변).

- `_activate_lock` 안에서:
  - `expected_current` 가 주어졌고 `current_preset_id != expected_current` 면 아무것도 바꾸지 않고 `(None, current_preset_id)` — 복귀 조건 검사를 프레임 교체와 같은 임계 구역에서 한다.
  - 교체 직전의 `current_preset_id` 를 `previous_id` 로 돌려준다.
  - 교체 후 `pending_revert` 가 있고 `current_preset_id != pending_revert["expected"]` 면 예약을 지우고 타이머를 취소한다 (직접 다른 프리셋으로 바꾼 경우 — Admin 표시도 바로 사라진다).
- 락은 기존 규칙대로 중첩하지 않는다. `persist_state()` 는 락을 푼 뒤 호출.

### 4.3 `until` 파서

```python
UNTIL_RE = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})?$"
```

- 오프셋이 없으면 UTC 로 본다 (Power Automate 의 `end` 필드가 오프셋 없는 UTC).
- 최대 64자. 해석 결과는 aware UTC `datetime`.
- `fromisoformat` 에 의존하지 않는다 (Python 3.10 은 7자리 소수 초·`Z` 를 못 읽는다).

### 4.4 라우트

| Method | Path | 인증 | 동작 |
|--------|------|------|------|
| `GET` | `/suggest` | 세션 | `static/suggest.html` (no-cache). 상태를 바꾸지 않는다 |
| `POST` | `/api/suggestions/apply` | 세션 | 본문 `{"preset_id": 8-hex, "until": str\|null}` (Pydantic, `extra="forbid"`) |
| `DELETE` | `/api/suggestions/revert` | 세션 | 복귀 예약 취소 → `{"cancelled": bool}` |
| `GET` | `/status` | 세션 | 기존 필드 + `"pending_revert": {"at", "revert_to_id", "revert_to_name"} \| null` |

모두 `PUBLIC_PATHS`·`API_KEY_PATHS` 에 넣지 않는다 (기본 보호). API 키로는 열리지 않는다.

`POST /api/suggestions/apply` 규칙:

1. 프리셋이 없으면 404.
2. `until` 이 주어졌고 해석되며 **이미 지났으면 409** "이미 끝난 일정입니다" — 적용하지 않는다.
3. `old = pending_revert` 를 먼저 떠 둔 뒤 `_activate_core(preset, force=False)` → `previous`.
4. 복귀 기준(baseline): `old` 가 있고 `previous == old["expected"]` 면 `old["revert_to"]` (연속 일정 — 처음 상태 유지), 아니면 `previous`.
5. `until` 이 해석되고 `now < until ≤ now + 24h` 이며 baseline 이 있고 `baseline != preset_id` 면 예약 `{baseline, preset_id, until}` 을 걸고 저장. 아니면 예약하지 않는다 (형식 오류·범위 밖·되돌릴 대상 없음은 적용만 하고 `revert: null`).
6. 응답: 기존 activate 결과 + `"revert": {"at", "revert_to_id", "revert_to_name"} | null`.

복귀 실행 (`at` 도달):

- 예약을 꺼낸 뒤 `revert_to` 프리셋이 없으면 로그만 남기고 끝.
- `_activate_core(revert_to, force=False, expected_current=expected)` — 조건이 안 맞으면(직접 바꿈) 아무것도 하지 않는다.
- 예약을 지우고 저장.

### 4.5 로그인 후 원래 링크로 돌아가기

```python
NEXT_RE = re.compile(r"^/(?:admin|suggest)(?:\?[^#\\\s]*)?$")   # 최대 1024자
```

- `auth_gate`: 미인증 브라우저 GET 의 리디렉션을 `/` 대신, 경로가 `/suggest` 면 `/?next=<경로+쿼리 URL 인코딩>` 으로 보낸다 (그 밖의 경로는 지금처럼 `/` — 로그인 후 기본 목적지가 이미 `/admin`).
- `/` (메인): 이미 로그인돼 있으면 유효한 `next` 또는 `/admin` 으로 303. `login.html` 의 JS 가 `next` 를 같은 규칙으로 검사해 버튼 링크를 `/login?next=…` 로 바꾼다 (JS 가 없으면 그냥 `/login`).
- `/login`: 유효한 `next` 면 `session["next"]` 에 저장, 아니면 지운다. 이미 로그인돼 있으면 바로 그리로 303.
- `/auth/callback` 성공: `session.clear()` **전에** `next` 를 꺼내 두고, 세션 발급 후 유효하면 그리로, 아니면 `/admin` 으로 303. 오류 분기는 기존대로 세션을 건드리지 않는다.
- 모든 지점에서 `NEXT_RE` 로 다시 검사한다 (`//evil.com`, `/\evil`, `https://…`, `/admin@evil`, `/suggest/../x`, 개행 등은 거부 → `/admin`).

### 4.6 화면

**`static/suggest.html`** — Admin 과 같은 토큰(Linear 다크), 휴대폰 우선, 단일 HTML/JS:

- 쿼리 `preset`, `until` 을 읽고 `/api/presets`, `/status` 를 조회.
- 이름이 정확히 같은 프리셋을 찾으면: 미리보기(1-bit) + "'회의 중'으로 바꾸시겠어요?" + "15:00(일정 종료)에 지금의 '근무 중'으로 돌아갑니다" (시각은 브라우저 로컬 시간) + **[바꾸기]** / [그대로 두기](→ `/admin`) + "다른 프리셋으로" 목록.
- 이름이 없으면 "'xxx' 프리셋이 없습니다" + 전체 목록에서 고르기.
- 이미 표시 중이면 "이미 '회의 중'이 표시 중입니다".
- `until` 이 지났으면 "이미 끝난 일정입니다" (적용 버튼 없음, Admin 링크만).
- 적용 후: "바꿨습니다 · 15:00 복귀 예약" (또는 전송 예약됨 안내) + Admin 링크.
- 모든 값은 `textContent` 로 렌더 (프리셋 이름·쿼리 값은 신뢰하지 않는다).

**`admin.html`** — "현재 디스플레이" 카드에 `pending_revert` 가 있을 때 "15:00에 '근무 중'으로 돌아갑니다 [취소]" 한 줄.

## 5. Power Automate 흐름 (문서 `docs/power-automate.md`)

흐름 A — 일정 제안:

1. 트리거 **When an upcoming event is starting soon (V3)**: Calendar Id = 기본 캘린더, Look-Ahead Time = 5.
2. 결과 `value` 의 각 일정에 대해 (Apply to each):
3. 조건: `isAllDay` 가 false, `showAs` ≠ `free`, `responseType` ≠ `declined`, 제목이 `Canceled:`/`취소됨:` 으로 시작하지 않음.
4. Compose `프리셋` — 식 하나로 키워드 매핑:
   `if(contains(toLower(item()?['subject']),'점심'),'점심 식사',if(contains(toLower(item()?['subject']),'외근'),'외근 중','회의 중'))`
5. Teams **Post message in a chat or channel** — Post as: Flow bot, Post in: Chat with Flow bot, Recipient: 나.
   메시지: 시작 시각(KST)·제목·"'<프리셋>'으로 바꾸시겠어요?" + 링크
   `https://<도메인>/suggest?preset=@{encodeUriComponent(outputs('프리셋'))}&until=@{encodeUriComponent(item()?['endWithTimeZone'])}`

흐름 B — 시간대 제안 (선택): Recurrence(주 단위, 월~금, 11:55, Korea Standard Time) → 같은 메시지, 프리셋 `점심 식사`,
`until = convertToUtc(concat(formatDateTime(convertFromUtc(utcNow(),'Korea Standard Time'),'yyyy-MM-dd'),'T13:00:00'),'Korea Standard Time')`.

대체 경로: 회사 Teams 에서 Workflows 앱(Flow bot)이 막혀 있으면 Office 365 Outlook **Send an email (V2)** 로 나에게 같은 링크를 보낸다.

문서에 함께 적을 것: 키워드 수정 위치, 테스트 방법(6분 뒤 테스트 일정), Teams 링크가 자체 브라우저로 열릴 때 처음 한 번 로그인, 링크에 제목을 넣지 않는 이유.

## 6. 오류 처리

| 상황 | 동작 |
|------|------|
| 링크의 프리셋 이름이 없음 | 화면에서 전체 목록 제시 (서버 오류 아님) |
| `until` 형식 오류·24시간 초과 | 적용은 하고 복귀 예약 없음 (`revert: null`) |
| `until` 이 지남 | 화면: 안내만. API: 409, 적용 안 함 |
| 복귀 시점에 직접 바꿔 둔 상태 | 아무것도 안 함, 예약 삭제 |
| 복귀 대상 프리셋이 삭제됨 | 로그만, 예약 삭제 |
| 서버 재시작 | state.json 에서 예약 복원, 지난 예약은 즉시 처리 |
| state.json 저장 실패 | 기존 규칙대로 로그만 (메모리의 예약은 유효) |
| 세션 만료 | 메인 → 로그인 → 원래 `/suggest…` 로 복귀 |

## 7. 보안 점검

- 새 HTTP 라우트는 모두 기본 보호(세션). `PUBLIC_PATHS`·`API_KEY_PATHS` 변경 없음.
- 상태 변경은 POST/DELETE 만. `GET /suggest` 는 아무것도 바꾸지 않는다 (SameSite=Lax 쿠키가 교차 사이트 GET 에 실리므로).
- 본문 크기는 기존 `MAX_JSON_BODY_SIZE` 를 따른다.
- `next` 는 허용 목록 정규식으로만 받는다 (open redirect 방지). 세션 쿠키 크기를 위해 1024자 제한.
- 링크는 자격 증명이 아니다. 링크만 가진 사람은 로그인 없이는 아무것도 못 한다.
- 로그에는 프리셋 이름·시각만 남긴다 (이메일·일정 제목 없음 — 서버는 제목을 받지도 않는다).

## 8. 테스트

기존 하네스(`.omc/scratch/tests`, git 제외)에 `t_suggest.py` 추가, `t_auth.py` 갱신. **Python 3.10 venv 로도 실행**한다 (VM 과 같은 버전).

- 적용: 활성화되고 `revert` 가 응답·`/status` 에 나타남.
- 복귀: `until` = 지금+2초 → 2~3초 뒤 이전 프리셋으로 돌아감.
- 직접 변경: 적용 후 다른 프리셋 활성화 → 예약 사라짐, 시각이 지나도 그대로.
- 연속 일정: A→(제안 B)→(제안 B, 더 늦은 until) → 복귀 대상은 A 유지, 시각만 늦춰짐.
- 다른 제안이 이어짐: A→(제안 B)→(제안 C) → 복귀 대상 A.
- `until` 형식 오류·25시간 뒤·지난 시각(409)·누락.
- 7자리 소수 초·`Z`·`+00:00`·`+09:00`·오프셋 없음 파싱.
- 재시작 복원: 예약 저장 → 재기동 → 시각에 복귀. 지난 예약은 기동 직후 처리.
- 취소: `DELETE /api/suggestions/revert`.
- 복귀 대상 삭제 시 예외 없이 예약만 삭제.
- 인증: 세 라우트 모두 미인증 401(브라우저 GET 은 `/?next=…` 303), 올바른 API 키로도 401.
- `next`: 정상 경로 복귀, 위험한 값 전부 `/admin` 으로.
- 화면: 데스크톱·모바일 스크린샷 (제안/없음/이미 표시 중/지난 일정/적용 후).

수동: Power Automate 에서 6분 뒤 테스트 일정 → Teams 메시지 → 링크 → 적용 → 종료 시 복귀.

## 9. 문서 갱신

- `docs/power-automate.md` (신규) — 흐름 A/B, 대체 경로, 테스트 방법.
- `README.md` — 기능 소개, API 표(3개 라우트 + `/status` 필드), 로그인 흐름의 `next`.
- `CLAUDE.md` — 제안·복귀 예약, `_activate_core`, state.json 키, `NEXT_RE`, Python 3.10 제약.
- `docs/google-oauth.md` — 로그인 후 원래 주소로 돌아가는 흐름 한 줄.
