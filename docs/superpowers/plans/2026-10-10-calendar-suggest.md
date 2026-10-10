# 일정 기반 상태 제안 (Calendar Suggest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Power Automate 가 보낸 Teams 링크(`/suggest?preset=…&until=…`)를 열어 한 번 눌러 프리셋을 바꾸고, 일정 종료 시각에 서버가 이전 프리셋으로 되돌린다.

**Architecture:** 서버(`server/main.py`, 단일 파일)에 복귀 예약 상태(`pending_revert`)와 타이머, 적용·취소 API, 확인 화면(`static/suggest.html`)을 더한다. 활성화 본체를 `_activate_core` 로 분리해 "직전 프리셋 기록"과 "복귀 조건 검사"를 기존 `_activate_lock` 임계 구역 안에서 처리한다. 로그인 전 연 `/suggest…` 주소는 허용 목록 검사 후 세션의 `next` 로 넘겨 로그인 뒤 돌아온다. Power Automate 흐름은 문서로만 제공한다.

**Tech Stack:** FastAPI/Starlette, Pydantic v2, asyncio, vanilla HTML/JS. 테스트는 `.omc/scratch/tests` 의 기존 하네스(httpx + in-process uvicorn + 실제 프로세스).

**Spec:** `docs/superpowers/specs/2026-10-10-calendar-suggest-design.md`

## Global Constraints

- 서버 VM 은 **Python 3.10.12** — 3.11+ 전용 API 금지 (`datetime.fromisoformat` 의 `Z`·7자리 소수 초, `datetime.UTC`, `asyncio.TaskGroup`, `typing.Self` 등). 모든 테스트를 3.10 venv 로도 한 번 돌린다.
- 새 HTTP 라우트(`/suggest`, `/api/suggestions/apply`, `/api/suggestions/revert`)는 **세션 전용** — `PUBLIC_PATHS`·`API_KEY_PATHS` 를 바꾸지 않는다.
- 상태 변경은 POST/DELETE 만. `GET /suggest` 는 아무것도 바꾸지 않는다.
- 락 3개는 중첩 금지, `persist_state()` 는 락을 푼 뒤 호출 (CLAUDE.md 규칙 그대로).
- `next` 허용: `^/(?:admin|suggest)(?:\?[^#\\\s]*)?$`, 최대 1024자.
- `until`: 최대 64자, 오프셋 없으면 UTC, 복귀 예약은 `now < until ≤ now + 24h` 일 때만, 지난 `until` 은 409.
- 로그에 이메일·일정 제목을 남기지 않는다. 문서에는 `<도메인>`, `you@gmail.com` 같은 자리표시자만.
- UI 는 Admin 의 디자인 토큰(Linear 다크)을 쓴다. 작은 정보성 글자는 `--ink-subtle` 이상.
- 펌웨어는 바꾸지 않는다.

## Review Focus

1. **한글 프리셋 이름의 링크 인코딩** — Power Automate `encodeUriComponent` 는 공백을 `%20` 으로, 손으로 만든 링크는 `+` 일 수 있다. 둘 다 같은 프리셋을 찾아야 한다 (Task 4, 브라우저 검사 4-6).
2. **Power Automate `end` 필드(오프셋 없음, 7자리 소수 초)** 를 잘못 고른 경우 — UTC 로 읽어 정상 예약돼야 한다 (Task 1 파서 테스트, Task 2 `S-until`).
3. **같은 Teams 링크를 두 번 열어 두 번 [바꾸기]** — 복귀 대상이 처음 상태로 남아야 한다 (Task 2 `S-apply` 마지막 검사).
4. **적용 후 일정이 끝나기 전에 서버 재배포·재시작** — 예약이 살아남고, 재시작 중 지난 예약은 기동 직후 처리 (Task 3 `S-restart`).
5. **세션이 만료된 상태에서 [바꾸기]** — API 가 401 을 주면 로그인 후 같은 제안 화면으로 돌아와야 한다 (Task 4 브라우저 검사 4-7, Task 5 `S-next`).

---

## File Structure

| 파일 | 책임 | 작업 |
|------|------|------|
| `server/main.py` | `parse_until`, `_safe_next`, `pending_revert` 상태·타이머·영속화, `_activate_core`, 적용·취소 API, `/status` 필드, `GET /suggest`, `next` 처리 | 수정 |
| `server/static/suggest.html` | 제안 확인 화면 (단일 HTML/JS) | 생성 |
| `server/static/login.html` | `next` 를 `/login?next=` 로 넘기는 스크립트 | 수정 |
| `server/static/admin.html` | "15:00에 '근무 중'으로 돌아갑니다 [취소]" 한 줄 | 수정 |
| `docs/power-automate.md` | 흐름 A/B, 대체 경로, 테스트, 문제 해결 | 생성 |
| `README.md`, `CLAUDE.md`, `docs/google-oauth.md` | API 표·상태 저장·로그인 흐름·규칙 | 수정 |
| `.omc/scratch/tests/harness.py`, `run_all.sh` | `TEST_PY` 로 인터프리터 교체, `t_suggest` 추가 | 수정 (git 제외) |
| `.omc/scratch/tests/t_suggest.py` | 파서·next·적용·복귀·재시작 테스트 | 생성 (git 제외) |
| `.omc/scratch/tests/t_auth.py` | 새 라우트 게이트, `next` 흐름 | 수정 (git 제외) |

테스트 하네스는 지금처럼 git 에서 제외된 `.omc/scratch/tests` 에 둔다. 커밋은 저장소 파일만 한다.

---

### Task 1: 순수 함수(`parse_until`, `_safe_next`)와 Python 3.10 테스트 환경

**Files:**
- Modify: `server/main.py` (import, 상수 — `PRESET_ID_RE` 정의 바로 아래, 함수 — `_now_iso()` 바로 아래)
- Modify: `.omc/scratch/tests/harness.py:25` (`PY`), `.omc/scratch/tests/run_all.sh`
- Create: `.omc/scratch/tests/t_suggest.py`

**Interfaces:**
- Produces: `parse_until(value) -> Optional[datetime]` (aware UTC 또는 None), `_safe_next(value) -> Optional[str]`, 상수 `UNTIL_RE`, `MAX_UNTIL_LENGTH = 64`, `MAX_REVERT_AHEAD = 24 * 3600`, `NEXT_RE`, `MAX_NEXT_LENGTH = 1024`. 하네스는 `TEST_PY` 환경변수로 서버 프로세스 인터프리터를 바꾼다.

- [ ] **Step 1: Python 3.10 venv 만들기**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
uv venv -p 3.10 .omc/scratch/venv310
uv pip install --python .omc/scratch/venv310/bin/python -r server/requirements.txt httpx requests
.omc/scratch/venv310/bin/python -V
```
Expected: `Python 3.10.x`

- [ ] **Step 2: 하네스가 `TEST_PY` 를 따르게 하기**

`.omc/scratch/tests/harness.py` 에서

```python
PY = str(ROOT / ".omc/scratch/venv/bin/python")
```
를
```python
PY = os.environ.get("TEST_PY") or str(ROOT / ".omc/scratch/venv/bin/python")   # 3.10 검증: TEST_PY=…/venv310/bin/python
```
로 바꾼다.

`.omc/scratch/tests/run_all.sh` 에서

```bash
PY=/Users/junja/Desktop/claude/eink-status-board/.omc/scratch/venv/bin/python
```
를
```bash
PY=${TEST_PY:-/Users/junja/Desktop/claude/eink-status-board/.omc/scratch/venv/bin/python}
export TEST_PY="$PY"
```
로, 그리고
```bash
for s in t_a_startup t_auth t_auth_nokeys t_features t_ratelimit t_concurrency t_persistence; do
```
를
```bash
for s in t_a_startup t_auth t_auth_nokeys t_features t_ratelimit t_concurrency t_persistence t_suggest; do
```
로 바꾼다.

- [ ] **Step 3: 실패하는 테스트 작성 — `.omc/scratch/tests/t_suggest.py`**

```python
"""S: 일정 제안 — until 파서·next 허용 목록(순수 함수), 적용·복귀 예약(AUTH_DISABLED, in-process 5096),
재시작 복원(실제 프로세스 5097)."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from harness import *  # noqa

PORT = 5096
UTC = timezone.utc
D = scenario_dir("suggest")
mod = load_main(D, {"AUTH_DISABLED": "true", "MIN_PUSH_INTERVAL": "0"})
note(f"python {sys.version.split()[0]}")

# ====================================================================== S-parse
G = "S-parse"
EXPECT = datetime(2026, 10, 10, 5, 0, tzinfo=UTC)
good = {
    "2026-10-10T05:00:00.0000000+00:00": EXPECT,   # Power Automate endWithTimeZone
    "2026-10-10T05:00:00.0000000": EXPECT,         # Power Automate end (오프셋 없음 = UTC)
    "2026-10-10T05:00:00.0000000Z": EXPECT,        # convertToUtc(...) 결과
    "2026-10-10T05:00:00Z": EXPECT,
    "2026-10-10T14:00:00+09:00": EXPECT,
    "2026-10-09T20:00:00-09:00": EXPECT,
    "2026-10-10T05:00": EXPECT,
    "2026-10-10T05:00:00.5+00:00": EXPECT,         # 소수 초는 버린다
    " 2026-10-10T05:00:00Z ": EXPECT,              # 앞뒤 공백
}
for raw, want in good.items():
    got = mod.parse_until(raw)
    check(G, f"parse_until({raw!r}) == {want.isoformat()}", got == want and got is not None and got.tzinfo is not None, repr(got))
bad = ["", "tomorrow", "2026-13-01T00:00:00Z", "2026-02-30T00:00:00Z", "2026-10-10T24:00:00Z",
       "2026-10-10T05:00:00+24:00", "2026-10-10T05:00:00+09:60", "2026-10-10 05:00:00Z",
       "2026-10-10T05:00:00.12345678Z", "2026-10-10T05:00:00+0900", "2026-10-10", "2" * 65,
       None, 123, ["2026-10-10T05:00:00Z"]]
for raw in bad:
    got = mod.parse_until(raw)
    check(G, f"parse_until({str(raw)[:40]!r}) is None", got is None, repr(got))

# ====================================================================== S-next
G = "S-next-pure"
for ok in ["/admin", "/suggest", "/suggest?", "/admin?x=1", "/suggest?next=//evil.example",
           "/suggest?preset=%ED%9A%8C%EC%9D%98%20%EC%A4%91&until=2026-10-10T05%3A00%3A00Z"]:
    check(G, f"_safe_next accepts {ok[:60]!r}", mod._safe_next(ok) == ok, repr(mod._safe_next(ok)))
for no in ["//evil.example", "/\\evil.example", "https://evil.example/admin", "/admin@evil.example",
           "/suggest/../status", "/suggestx", "/suggest#x", "/suggest?a b", "/suggest?a\nb", "/suggest?a\u0085b",
           "/", "", None, 42, "/Admin", "/admin/", " /admin", "\\/admin", "/suggest?" + "a" * 1100]:
    check(G, f"_safe_next rejects {str(no)[:40]!r}", mod._safe_next(no) is None, repr(mod._safe_next(no)))

finish()
```

- [ ] **Step 4: 실패 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_suggest.py 2>&1 | tail -5`
Expected: `AttributeError: module 'main' has no attribute 'parse_until'`

- [ ] **Step 5: 구현 — `server/main.py`**

import 두 줄을 바꾼다:

```python
from datetime import datetime, timezone
```
→
```python
from datetime import datetime, timedelta, timezone
```
그리고
```python
from urllib.parse import urlsplit
```
→
```python
from urllib.parse import quote, urlsplit
```

`PRESET_ID_RE = re.compile(r"[0-9a-f]{8}")` 바로 아래에 추가:

```python
# 일정 제안 링크의 종료 시각 (Power Automate 의 endWithTimeZone / end / convertToUtc 결과).
# Python 3.10 의 fromisoformat 은 7자리 소수 초와 Z 를 못 읽어 직접 해석한다. 소수 초는 버린다
UNTIL_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})?"
)
MAX_UNTIL_LENGTH = 64
MAX_REVERT_AHEAD = 24 * 3600          # 복귀 예약은 24시간 안쪽 일정만
# 로그인 후 돌아갈 수 있는 주소 — 허용 목록 (open redirect 방지)
NEXT_RE = re.compile(r"/(?:admin|suggest)(?:\?[^#\\\s]*)?")
MAX_NEXT_LENGTH = 1024                # 세션 쿠키(4KB)에 들어가므로 짧게
```

`def _now_iso() -> str:` 함수 바로 아래에 추가:

```python
def parse_until(value) -> Optional[datetime]:
    """일정 종료 시각 문자열을 aware UTC datetime 으로. 형식이 틀리면 None. 오프셋이 없으면 UTC 로 본다."""
    if not isinstance(value, str) or len(value) > MAX_UNTIL_LENGTH:
        return None
    m = UNTIL_RE.fullmatch(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, offset = m.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0),
                          tzinfo=timezone.utc)
    except ValueError:
        return None
    if offset and offset != "Z":
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        moment = moment - delta if offset[0] == "+" else moment + delta
    return moment


def _safe_next(value) -> Optional[str]:
    """로그인 후 돌아갈 주소. 허용 목록(/admin, /suggest…)에 맞으면 그대로, 아니면 None."""
    if isinstance(value, str) and len(value) <= MAX_NEXT_LENGTH and NEXT_RE.fullmatch(value):
        return value
    return None
```

- [ ] **Step 6: 통과 확인 (3.14 와 3.10 둘 다)**

Run:
```bash
cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests
../venv/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL"
TEST_PY=../venv310/bin/python ../venv310/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL|^NOTE python"
```
Expected: 두 번 모두 `SUMMARY t_suggest: 49 passed, 0 failed`, 두 번째는 `NOTE python 3.10.x`

- [ ] **Step 7: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/main.py
git commit -m "feat(suggest): until parser and login-return allowlist"
```

---

### Task 2: `_activate_core`, 복귀 예약, 적용·취소 API, `/status` 필드

**Files:**
- Modify: `server/main.py` — 전역(`_persist_task` 선언 아래), `activate()`, `/status`, 프리셋 삭제, 새 라우트(`activate_preset` 라우트 아래), import(`ConfigDict`)
- Test: `.omc/scratch/tests/t_suggest.py` (Task 1 블록 뒤, `finish()` 앞)

**Interfaces:**
- Consumes: `parse_until`, `MAX_UNTIL_LENGTH`, `MAX_REVERT_AHEAD` (Task 1)
- Produces:
  - 전역 `pending_revert: Optional[dict]` — `{"revert_to": str, "expected": str, "at": datetime(aware UTC)}`, `_revert_task: Optional[asyncio.Task]`
  - `async def _activate_core(preset: dict, force: bool, expected_current: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]`
  - `activate(preset, force) -> dict` (기존과 같은 반환)
  - `_schedule_revert() -> None`, `_clear_pending_revert() -> None`, `_revert_info() -> Optional[dict]` (`{"at": iso, "revert_to_id", "revert_to_name"}`)
  - `POST /api/suggestions/apply` (`SuggestionApplyRequest{preset_id, until}`) → activate 결과 + `"revert"`
  - `DELETE /api/suggestions/revert` → `{"cancelled": bool}`
  - `/status` 의 `"pending_revert"`

- [ ] **Step 1: 실패하는 테스트 추가 — `t_suggest.py` 의 `finish()` 바로 위**

```python
# ====================================================================== 적용·복귀 (in-process)
srv = LiveServer(mod.app, PORT).start()
c = httpx.Client(base_url=srv.base, timeout=15)


def mk(name):
    r = c.post("/api/presets/text", json={"name": name})
    r.raise_for_status()
    return r.json()["id"]


def act(pid):
    c.post(f"/api/presets/{pid}/activate").raise_for_status()


def st():
    return c.get("/status").json()


def at(seconds):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def apply(pid, until=None):
    return c.post("/api/suggestions/apply", json={"preset_id": pid, "until": until})


def wait_active(pid, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if st()["active_preset_id"] == pid:
            return True
        time.sleep(0.2)
    return False


A, B, C = mk("근무 중"), mk("회의 중"), mk("외근 중")

G = "S-apply"
r = apply(B, at(60))
check(G, "nothing shown yet -> applied, no revert (nothing to return to)",
      r.status_code == 200 and r.json()["revert"] is None and st()["active_preset_id"] == B, r.text[:200])
act(A)
r = apply(B, at(60))
body = r.json()
check(G, "A shown, apply B -> 200, B active", r.status_code == 200 and st()["active_preset_id"] == B, r.text[:200])
check(G, "response.revert points back to A by id and name",
      (body.get("revert") or {}).get("revert_to_id") == A and body["revert"]["revert_to_name"] == "근무 중", str(body.get("revert")))
check(G, "response keeps activate() fields", {"success", "message", "clients_notified", "skipped", "deferred"} <= set(body), str(sorted(body)))
pr = st()["pending_revert"]
check(G, "/status.pending_revert mirrors the reservation",
      bool(pr) and pr["revert_to_id"] == A and pr["revert_to_name"] == "근무 중" and mod.parse_until(pr["at"]) is not None, str(pr))
r = apply(B, at(60))
check(G, "same link applied twice keeps A as the return target",
      r.status_code == 200 and st()["pending_revert"]["revert_to_id"] == A, str(st()["pending_revert"]))

G = "S-revert"
act(A)
apply(B, at(2))
check(G, "revert fires at until -> A again", wait_active(A, 6), str(st()))
check(G, "...and the reservation is gone", st()["pending_revert"] is None, str(st()["pending_revert"]))

G = "S-diverge"
act(A)
apply(B, at(2))
act(C)
check(G, "manual switch to C clears the reservation immediately", st()["pending_revert"] is None, str(st()["pending_revert"]))
time.sleep(3)
check(G, "...and nothing reverts at until", st()["active_preset_id"] == C, str(st()))

G = "S-chain"
act(A)
apply(B, at(60))
apply(B, at(2))
pr = st()["pending_revert"]
check(G, "back-to-back same suggestion: target stays A, time replaced",
      bool(pr) and pr["revert_to_id"] == A and mod.parse_until(pr["at"]) < datetime.now(UTC) + timedelta(seconds=10), str(pr))
check(G, "...reverts to A at the new until", wait_active(A, 6), str(st()))
act(A)
apply(B, at(60))
r = apply(C, at(2))
check(G, "A -> suggest B -> suggest C: return target is still A", (r.json().get("revert") or {}).get("revert_to_id") == A, r.text[:200])
check(G, "...reverts to A", wait_active(A, 6), str(st()))

G = "S-until"
act(A)
r = apply(B, at(-60))
check(G, "ended event -> 409, nothing applied", r.status_code == 409 and st()["active_preset_id"] == A, f"{r.status_code} {st()['active_preset_id']}")
for label, until in (("garbage", "garbage"), ("25h ahead", at(25 * 3600)), ("missing", None)):
    act(A)
    r = apply(B, until)
    check(G, f"until {label} -> applied without revert",
          r.status_code == 200 and r.json()["revert"] is None and st()["active_preset_id"] == B and st()["pending_revert"] is None, r.text[:200])
act(A)
pa_end = (datetime.now(UTC) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S.0000000")
r = apply(B, pa_end)
rv = r.json().get("revert") or {}
check(G, "Power Automate 'end' (no offset, 7 digits) is read as UTC",
      r.status_code == 200 and rv and abs((mod.parse_until(rv["at"]) - datetime.now(UTC)).total_seconds() - 60) < 5, r.text[:200])
act(A)

G = "S-validate"
for label, payload, code in (
    ("unknown id", {"preset_id": "ffffffff", "until": None}, 404),
    ("bad id format", {"preset_id": "../x", "until": None}, 422),
    ("extra field", {"preset_id": B, "until": None, "force": True}, 422),
    ("until too long", {"preset_id": B, "until": "2" * 65}, 422),
    ("until not a string", {"preset_id": B, "until": 123}, 422),
):
    r = c.post("/api/suggestions/apply", json=payload)
    check(G, f"{label} -> {code}", r.status_code == code, f"{r.status_code} {r.text[:120]}")

G = "S-cancel"
act(A)
apply(B, at(60))
r = c.delete("/api/suggestions/revert")
check(G, "DELETE -> cancelled: true, reservation gone",
      r.status_code == 200 and r.json() == {"cancelled": True} and st()["pending_revert"] is None, r.text)
r = c.delete("/api/suggestions/revert")
check(G, "second DELETE -> cancelled: false", r.json() == {"cancelled": False}, r.text)

G = "S-delete"
T1, T2 = mk("임시 기본"), mk("임시 제안")
act(T1)
apply(B, at(60))
r = c.delete(f"/api/presets/{T1}")
check(G, "deleting the return target drops the reservation", r.status_code == 200 and st()["pending_revert"] is None, str(st()["pending_revert"]))
check(G, "...and the display stays on B", st()["active_preset_id"] == B, str(st()))
act(A)
apply(T2, at(60))
r = c.delete(f"/api/presets/{T2}")
check(G, "deleting the suggested preset drops the reservation", r.status_code == 200 and st()["pending_revert"] is None, str(st()["pending_revert"]))
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|Error" | head`
Expected: `KeyError: 'revert'` (적용 라우트가 없어 404 JSON) 로 중단

- [ ] **Step 3: 구현 — import 와 전역**

```python
from pydantic import BaseModel, Field
```
→
```python
from pydantic import BaseModel, ConfigDict, Field
```

`_persist_task: Optional[asyncio.Task] = None` 바로 아래에 추가:

```python

# 제안을 받아 바꾼 프리셋을 일정 종료 시각에 되돌리는 예약 (하나만 유지, state.json 에 저장)
# {"revert_to": 바꾸기 직전 프리셋 id, "expected": 제안으로 적용한 id, "at": aware UTC datetime}
pending_revert: Optional[dict] = None
_revert_task: Optional[asyncio.Task] = None
```

- [ ] **Step 4: 구현 — `activate()` 를 `_activate_core` 로 분리**

기존 `async def activate(preset: dict, force: bool) -> dict:` 함수 전체(독스트링부터 `return { ... "deferred": deferred, }` 까지)를 아래로 교체한다:

```python
async def _activate_core(
    preset: dict, force: bool, expected_current: Optional[str] = None
) -> tuple[Optional[dict], Optional[str]]:
    """
    activate() 의 본체. (결과, 교체 직전의 current_preset_id) 를 돌려준다.

    expected_current 가 주어졌는데 지금 표시 중인 프리셋이 그것이 아니면 아무것도 바꾸지 않고
    (None, 현재 id) 를 돌려준다 — 복귀 조건 검사와 "직전 프리셋" 기록을 프레임 교체와 같은
    _activate_lock 안에서 해, 동시에 들어온 활성화와 엇갈리지 않게 한다.
    """
    global current_status_text, current_preset_id, current_frame_bytes, current_display_image, pending_revert

    try:
        img = await asyncio.to_thread(load_preset_image, preset["id"])
    except FileNotFoundError:
        raise HTTPException(404, "프리셋의 이미지 파일을 찾을 수 없습니다.")

    frame_bytes = await asyncio.to_thread(image_to_1bit_bytes, img)

    async with _activate_lock:
        if expected_current is not None and current_preset_id != expected_current:
            return None, current_preset_id

        # 이미지를 읽는 사이에 이름이 바뀌었거나 삭제됐을 수 있으므로 락 안에서 다시 조회한다
        preset = await asyncio.to_thread(get_preset, preset["id"])
        if preset is None:
            raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

        previous = current_preset_id
        unchanged = frame_bytes == current_frame_bytes and not force
        push_pending = _deferred_push is not None and not _deferred_push.done()

        current_status_text = preset["name"]
        current_preset_id = preset["id"]
        current_display_image = img
        current_frame_bytes = frame_bytes

        # 제안으로 바꾼 상태에서 벗어나면(직접 다른 프리셋을 고름) 복귀 예약은 지킬 이유가 없다
        if pending_revert is not None and current_preset_id != pending_revert["expected"]:
            _clear_pending_revert()

        if unchanged:
            # 같은 프레임이 아직 전송 대기 중이면 "이미 표시 중"이 아니라 "예약됨"으로 알린다
            notified, deferred, skipped = 0, push_pending, not push_pending
        else:
            skipped = False
            notified, deferred = await push_current_frame(force)

    await persist_state()

    if skipped:
        logger.info(f"⏭️  프리셋 '{preset['name']}' — 화면이 이미 같아 전송 생략")
        message = f"'{preset['name']}' 이미 표시 중 (전송 생략)"
    elif deferred:
        logger.info(f"⏳ 프리셋 '{preset['name']}' — 전송 간격 제한으로 예약")
        message = f"'{preset['name']}' 활성화 — 잠시 후 화면에 반영됩니다"
    else:
        logger.info(f"✅ 프리셋 활성화: '{preset['name']}' → {notified}개 디바이스")
        message = f"'{preset['name']}' 활성화 완료"

    return {
        "success": True,
        "message": message,
        "clients_notified": notified,
        "skipped": skipped,
        "deferred": deferred,
    }, previous


async def activate(preset: dict, force: bool) -> dict:
    """
    프리셋을 현재 화면으로 만들고 디바이스에 푸시한다.

    프레임이 지금 표시 중인 것과 바이트 단위로 같으면 전송을 생략한다
    (E-ink 는 갱신 횟수가 수명이므로 불필요한 리프레시를 피한다).
    force=True 면 force_refresh 메시지를 먼저 보내 펌웨어 쪽 CRC 검사도 건너뛰게 한다.
    실제 전송은 push_current_frame() 의 간격 제한을 따른다.
    """
    result, _ = await _activate_core(preset, force)
    return result


# ──────────────────────────────────────────────
#  일정 제안 — 복귀 예약
# ──────────────────────────────────────────────

def _schedule_revert() -> None:
    """pending_revert 의 시각에 맞춰 복귀 타이머를 다시 건다. 예약이 없으면 타이머만 끈다."""
    global _revert_task
    if _revert_task is not None and not _revert_task.done() and _revert_task is not asyncio.current_task():
        _revert_task.cancel()
    _revert_task = asyncio.create_task(_revert_later(pending_revert)) if pending_revert is not None else None


def _clear_pending_revert() -> None:
    global pending_revert
    pending_revert = None
    _schedule_revert()


async def _revert_later(entry: dict) -> None:
    delay = (entry["at"] - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await _run_revert(entry)
    except Exception as e:      # 타이머 작업의 예외는 아무도 받지 않으므로 여기서 남긴다
        logger.error(f"❌ 일정 종료 복귀 실패: {e!r}")


async def _run_revert(entry: dict) -> None:
    """일정 종료 시각. 그때도 제안한 프리셋이 표시 중일 때만 직전 프리셋으로 되돌린다."""
    global pending_revert, _revert_task
    if pending_revert is not entry:          # 그 사이 취소·교체됐다
        return
    pending_revert = None
    _revert_task = None

    preset = await asyncio.to_thread(get_preset, entry["revert_to"])
    if preset is None:
        logger.info("↩️  일정 종료 — 되돌릴 프리셋이 삭제돼 그대로 둡니다")
    else:
        result, _ = await _activate_core(preset, force=False, expected_current=entry["expected"])
        if result is None:
            logger.info("↩️  일정 종료 — 그 사이 다른 프리셋으로 바뀌어 그대로 둡니다")
        else:
            logger.info(f"↩️  일정 종료 — '{preset['name']}' 으로 복귀")
    await persist_state()


def _revert_info() -> Optional[dict]:
    """Admin·제안 화면에 보여줄 복귀 예약. 없으면 None."""
    entry = pending_revert
    if entry is None:
        return None
    preset = get_preset(entry["revert_to"])
    return {
        "at": entry["at"].isoformat(timespec="seconds"),
        "revert_to_id": entry["revert_to"],
        "revert_to_name": preset["name"] if preset else None,
    }
```

- [ ] **Step 5: 구현 — 적용·취소 라우트**

`activate_preset` 라우트(`@app.post("/api/presets/{preset_id}/activate")` 함수) 바로 아래에 추가:

```python
class SuggestionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str = Field(pattern=r"^[0-9a-f]{8}$")
    until: Optional[str] = Field(default=None, max_length=MAX_UNTIL_LENGTH)


@app.post("/api/suggestions/apply")
async def apply_suggestion(req: SuggestionApplyRequest):
    """
    제안 화면의 [바꾸기]. 프리셋을 적용하고, 일정 종료 시각(until)에 바꾸기 직전 프리셋으로
    돌아가도록 예약한다. 연속 일정이면 처음 상태를 복귀 대상으로 유지하고 시각만 바꾼다.
    until 이 없거나 형식이 틀리거나 24시간 넘게 남았으면 적용만 한다. 이미 지났으면 409.
    """
    global pending_revert

    preset = await asyncio.to_thread(get_preset, req.preset_id)
    if not preset:
        raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

    now = datetime.now(timezone.utc)
    until = parse_until(req.until) if req.until else None
    if until is not None and until <= now:
        raise HTTPException(409, "이미 끝난 일정입니다.")

    old = pending_revert                     # 적용하면서 지워질 수 있으므로 먼저 떠 둔다
    result, previous = await _activate_core(preset, force=False)
    baseline = old["revert_to"] if old is not None and previous == old["expected"] else previous

    if (until is not None and until <= now + timedelta(seconds=MAX_REVERT_AHEAD)
            and baseline is not None and baseline != preset["id"]):
        pending_revert = {"revert_to": baseline, "expected": preset["id"], "at": until}
        _schedule_revert()
        await persist_state()
        logger.info(f"📅 제안 적용: '{preset['name']}' — {until.isoformat(timespec='minutes')} 에 복귀 예약")
    else:
        logger.info(f"📅 제안 적용: '{preset['name']}' (복귀 예약 없음)")

    return {**result, "revert": await asyncio.to_thread(_revert_info)}


@app.delete("/api/suggestions/revert")
async def cancel_revert():
    """일정 종료 복귀 예약을 취소한다 (Admin 의 [취소])."""
    cancelled = pending_revert is not None
    if cancelled:
        _clear_pending_revert()
        await persist_state()
        logger.info("📅 복귀 예약 취소")
    return {"cancelled": cancelled}
```

- [ ] **Step 6: 구현 — `/status` 필드와 프리셋 삭제**

`get_status` 반환 dict 의 마지막 항목

```python
        "user": None if AUTH_DISABLED else _session_email(request),
    }
```
을
```python
        "user": None if AUTH_DISABLED else _session_email(request),
        "pending_revert": await asyncio.to_thread(_revert_info),
    }
```
로 바꾼다.

`delete_preset` 의

```python
    async with _activate_lock:
        was_active = current_preset_id == preset_id
        if was_active:
            current_preset_id = None    # 재시작 시 복원 대상에서 제외
    if was_active:
        await persist_state()
```
를
```python
    async with _activate_lock:
        was_active = current_preset_id == preset_id
        if was_active:
            current_preset_id = None    # 재시작 시 복원 대상에서 제외
        # 복귀 예약이 지운 프리셋을 가리키면 지킬 수 없으므로 함께 지운다
        revert_dropped = pending_revert is not None and preset_id in (
            pending_revert["revert_to"], pending_revert["expected"])
        if revert_dropped:
            _clear_pending_revert()
    if was_active or revert_dropped:
        await persist_state()
```
로 바꾼다.

- [ ] **Step 7: 통과 확인**

Run:
```bash
cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests
../venv/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL"
TEST_PY=../venv310/bin/python ../venv310/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL"
```
Expected: 두 번 모두 `0 failed`

- [ ] **Step 8: 기존 기능 회귀 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && for s in t_features t_concurrency t_ratelimit; do ../venv/bin/python $s.py 2>&1 | grep "^SUMMARY"; done`
Expected: 세 줄 모두 `0 failed`

- [ ] **Step 9: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/main.py
git commit -m "feat(suggest): apply suggestion with revert-at-event-end reservation"
```

---

### Task 3: 복귀 예약의 영속화·재시작 복원

**Files:**
- Modify: `server/main.py` — `persist_state()` 스냅샷, `restore_state()`, `lifespan()`
- Test: `.omc/scratch/tests/t_suggest.py` (`finish()` 앞)

**Interfaces:**
- Consumes: `pending_revert`, `_schedule_revert`, `parse_until`, `PRESET_ID_RE`
- Produces: state.json 키 `"pending_revert": {"revert_to", "expected", "at"} | null`, `_pending_revert_json() -> Optional[dict]`

- [ ] **Step 1: 실패하는 테스트 추가 — `finish()` 바로 위**

```python
# ====================================================================== S-restart (실제 프로세스)
G = "S-restart"
PORT2 = 5097
d2 = scenario_dir("suggest-restart")
ps = ProcServer(d2, PORT2, {"MIN_PUSH_INTERVAL": "0"})
check(G, "server starts", ps.start(), "start failed")
pc = httpx.Client(base_url=ps.base, timeout=15)


def pmk(name):
    r = pc.post("/api/presets/text", json={"name": name})
    r.raise_for_status()
    return r.json()["id"]


def pst():
    return pc.get("/status").json()


def p_wait_active(pid, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pst()["active_preset_id"] == pid:
            return True
        time.sleep(0.3)
    return False


RA, RB = pmk("근무 중"), pmk("회의 중")
pc.post(f"/api/presets/{RA}/activate").raise_for_status()
pc.post("/api/suggestions/apply", json={"preset_id": RB, "until": at(10)}).raise_for_status()
ps.stop()
saved = json.loads((d2 / "data" / "state.json").read_text(encoding="utf-8"))
sp = saved.get("pending_revert") or {}
check(G, "state.json holds pending_revert after shutdown",
      sp.get("revert_to") == RA and sp.get("expected") == RB and mod.parse_until(sp.get("at")) is not None, str(sp))
check(G, "restart", ps.start(), ps.log()[-400:])
pr = pst()["pending_revert"]
check(G, "reservation restored after restart", bool(pr) and pr["revert_to_id"] == RA, str(pr))
check(G, "...and fires at until after the restart", p_wait_active(RA, 14), str(pst()))

pc.post("/api/suggestions/apply", json={"preset_id": RB, "until": at(2)}).raise_for_status()
ps.stop()
time.sleep(3)
check(G, "restart after until passed while down", ps.start(), ps.log()[-400:])
check(G, "overdue reservation handled right after startup", p_wait_active(RA, 4), str(pst()))
check(G, "...and cleared from state", pst()["pending_revert"] is None, str(pst()["pending_revert"]))

for label, bad in (("bad id", {"revert_to": "../x", "expected": RB, "at": at(60)}),
                   ("bad time", {"revert_to": RA, "expected": RB, "at": "soon"}),
                   ("not an object", "junk"), ("number", 5)):
    ps.stop()
    state_file = d2 / "data" / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["pending_revert"] = bad
    state_file.write_text(json.dumps(state), encoding="utf-8")
    started = ps.start()
    check(G, f"corrupt pending_revert ({label}) is dropped, server healthy",
          started and pst()["pending_revert"] is None, ps.log()[-300:] if not started else str(pst()["pending_revert"]))
ps.stop()
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_suggest.py 2>&1 | grep -E "FAIL \[S-restart\]" | head -3`
Expected: `FAIL [S-restart] state.json holds pending_revert after shutdown`

- [ ] **Step 3: 구현 — 저장**

`async def persist_state() -> bool:` 바로 위에 추가:

```python
def _pending_revert_json() -> Optional[dict]:
    entry = pending_revert
    if entry is None:
        return None
    return {"revert_to": entry["revert_to"], "expected": entry["expected"],
            "at": entry["at"].isoformat(timespec="seconds")}
```

`persist_state()` 의 스냅샷

```python
        snapshot = {
            "active_preset_id": current_preset_id,
            "session_generation": session_generation,
            "devices": {device_id: dict(info) for device_id, info in known_devices.items()},
        }
```
을
```python
        snapshot = {
            "active_preset_id": current_preset_id,
            "session_generation": session_generation,
            "devices": {device_id: dict(info) for device_id, info in known_devices.items()},
            "pending_revert": _pending_revert_json(),
        }
```
로 바꾼다.

- [ ] **Step 4: 구현 — 복원**

`restore_state()` 의 (같은 줄이 `logout()` 에도 있으니 이 블록으로 찾는다)

```python
    global session_generation

    state = load_state()
```
를
```python
    global session_generation, pending_revert

    state = load_state()
```
로 바꾸고, 디바이스 복원 블록(`devices = state.get("devices")` … `known_devices[device_id] = entry`) 바로 아래, `preset_id = state.get("active_preset_id")` 위에 추가:

```python
    # 일정 종료 복귀 예약 — 두 ID 와 시각이 모두 올바를 때만 (지난 시각이면 lifespan 이 곧바로 처리)
    raw = state.get("pending_revert")
    if isinstance(raw, dict):
        revert_to, expected, moment = raw.get("revert_to"), raw.get("expected"), parse_until(raw.get("at"))
        if (moment is not None and all(isinstance(i, str) and PRESET_ID_RE.fullmatch(i) for i in (revert_to, expected))):
            pending_revert = {"revert_to": revert_to, "expected": expected, "at": moment}
```

- [ ] **Step 5: 구현 — lifespan 타이머**

`lifespan()` 의

```python
    # 세션 세대를 바로 저장해 둔다 — 다음 재시작에도 같은 값을 써서 로그인이 유지되게
    await persist_state()
    logger.info("========================================")
    yield
    # 종료 시 예약돼 있던 디바이스 이력 저장을 마무리한다
    if _persist_task is not None and not _persist_task.done():
        _persist_task.cancel()
        await asyncio.gather(_persist_task, return_exceptions=True)
    await persist_state()
```
를
```python
    # 세션 세대를 바로 저장해 둔다 — 다음 재시작에도 같은 값을 써서 로그인이 유지되게
    await persist_state()
    # 복원된 일정 종료 복귀 예약 (이미 지난 시각이면 곧바로 처리된다)
    _schedule_revert()
    logger.info("========================================")
    yield
    # 복귀 타이머는 끄기만 한다 — 예약 자체는 state.json 에 남아 다음 시작 때 이어진다
    if _revert_task is not None and not _revert_task.done():
        _revert_task.cancel()
        await asyncio.gather(_revert_task, return_exceptions=True)
    # 종료 시 예약돼 있던 디바이스 이력 저장을 마무리한다
    if _persist_task is not None and not _persist_task.done():
        _persist_task.cancel()
        await asyncio.gather(_persist_task, return_exceptions=True)
    await persist_state()
```
로 바꾼다.

- [ ] **Step 6: 통과 확인 (3.14·3.10)과 영속화 회귀**

Run:
```bash
cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests
../venv/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL"
TEST_PY=../venv310/bin/python ../venv310/bin/python t_suggest.py 2>&1 | grep -E "^SUMMARY|FAIL"
../venv/bin/python t_persistence.py 2>&1 | grep -E "^SUMMARY"
```
Expected: 모두 `0 failed`

- [ ] **Step 7: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/main.py
git commit -m "feat(suggest): persist the revert reservation across restarts"
```

---

### Task 4: 제안 화면 `GET /suggest` + `static/suggest.html`

**Files:**
- Modify: `server/main.py` — `/admin` 라우트 바로 아래
- Create: `server/static/suggest.html`
- Test: `.omc/scratch/tests/t_auth.py` (게이트), 브라우저 검사(chrome-devtools)

**Interfaces:**
- Consumes: `GET /api/presets`(id·name 목록), `GET /status`(`text`, `active_preset_id`, `pending_revert`), `POST /api/suggestions/apply`, 프리셋 미리보기 `GET /api/presets/{id}/preview.png`
- Produces: `GET /suggest?preset=<이름>&until=<시각>` 화면. 401 이면 `/?next=<현재 주소>` 로 이동 (Task 5 가 받아 처리)

- [ ] **Step 1: 실패하는 게이트 테스트 추가 — `t_auth.py`**

`other = [("GET", "/api/presets"), …` 목록의 마지막 항목

```python
         ("GET", "/ws")]
```
을
```python
         ("GET", "/ws"), ("GET", "/suggest"), ("POST", "/api/suggestions/apply"), ("DELETE", "/api/suggestions/revert")]
```
로 바꾼다. 그리고 `# ====================================================================== H` 줄 바로 위에 추가:

```python
# ====================================================================== S (일정 제안: 게이트)
G = "S-suggest-auth"
for m, pth in (("GET", "/suggest?preset=x"), ("POST", "/api/suggestions/apply"), ("DELETE", "/api/suggestions/revert")):
    for label, h in (("no credentials", None), ("X-API-Key", KEY_HDR), ("Bearer", BEARER_HDR)):
        HITS.clear()
        r = rq(m, pth, headers=h, json={"preset_id": SEED_ID, "until": None} if m == "POST" else None)
        check(G, f"{m} {pth} with {label} -> 401, no handler", r.status_code == 401 and not HITS, f"{r.status_code} {HITS}")
r = rq("GET", "/suggest?preset=x", headers=OK_COOKIE)
check(G, "GET /suggest with a session -> 200 page, no-cache",
      r.status_code == 200 and "<html" in r.text.lower() and r.headers.get("cache-control") == "no-cache", str(r.status_code))
r = rq("POST", "/api/suggestions/apply", headers=OK_COOKIE, json={"preset_id": SEED_ID, "until": None})
check(G, "POST /api/suggestions/apply with a session -> 200", r.status_code == 200 and r.json()["revert"] is None, r.text[:200])
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_auth.py 2>&1 | grep -E "FAIL \[S-suggest-auth\]"`
Expected: `FAIL [S-suggest-auth] GET /suggest with a session -> 200 page, no-cache`

- [ ] **Step 3: 구현 — 라우트**

`admin_page()` 라우트 함수 바로 아래에 추가:

```python
@app.get("/suggest")
async def suggest_page():
    """일정 제안 확인 화면. 열기만 해서는 아무것도 바뀌지 않는다 (적용은 POST /api/suggestions/apply)."""
    page = STATIC_DIR / "suggest.html"
    if not page.exists():
        return JSONResponse(status_code=404, content={"error": "suggest.html 파일을 찾을 수 없습니다."})
    return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache"})
```

- [ ] **Step 4: 구현 — `server/static/suggest.html`**

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#010102">
  <title>상태 제안 — E-ink Status Board</title>
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    /* 색·글꼴 값은 admin.html 의 디자인 토큰과 같다 */
    :root {
      --canvas: #010102;
      --surface-1: #0f1011;
      --surface-2: #141516;
      --surface-3: #18191a;
      --hairline: #23252a;
      --hairline-strong: #34343a;
      --hairline-tertiary: #3e3e44;
      --ink: #f7f8f8;
      --ink-muted: #d0d6e0;
      --ink-subtle: #8a8f98;
      --primary: #5e6ad2;
      --primary-hover: #828fff;
      --primary-focus: #5e69d1;
      --danger: #eb5757;
      --font: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    [hidden] { display: none !important; }
    body {
      min-height: 100vh;
      background: var(--canvas);
      color: var(--ink);
      font-family: var(--font);
      font-size: 14px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    :focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
    .topbar {
      display: flex;
      align-items: center;
      gap: 10px;
      height: 56px;
      padding: 0 16px;
      border-bottom: 1px solid var(--hairline);
      color: var(--ink);
      font-size: 14px;
      font-weight: 600;
      text-decoration: none;
    }
    main { max-width: 440px; margin: 0 auto; padding: 28px 16px 64px; }
    .card {
      padding: 24px 20px;
      background: var(--surface-1);
      border: 1px solid var(--hairline);
      border-radius: 12px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
    }
    .eyebrow { margin-bottom: 8px; font-size: 13px; font-weight: 500; letter-spacing: 0.4px; color: var(--ink-subtle); }
    h1 { font-size: 22px; font-weight: 600; line-height: 1.3; letter-spacing: -0.4px; overflow-wrap: anywhere; }
    .sub { margin-top: 8px; color: var(--ink-subtle); overflow-wrap: anywhere; }
    .bezel {
      margin-top: 20px;
      padding: 8px;
      background: var(--surface-3);
      border: 1px solid var(--hairline-strong);
      border-radius: 10px;
    }
    .screen { aspect-ratio: 416 / 240; overflow: hidden; background: #fff; border-radius: 2px; }
    .screen img { display: block; width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }
    .error { margin-top: 12px; font-size: 13px; color: var(--danger); }
    .actions { display: flex; flex-direction: column; gap: 8px; margin-top: 20px; }
    .btn {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 44px;
      padding: 0 14px;
      border: 1px solid transparent;
      border-radius: 8px;
      font-family: inherit;
      font-size: 15px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: background-color 0.15s ease, border-color 0.15s ease;
    }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover:not(:disabled) { background: var(--primary-hover); }
    .btn-primary:active:not(:disabled) { background: var(--primary-focus); }
    .btn-secondary { background: var(--surface-2); border-color: var(--hairline-strong); color: var(--ink); }
    .btn-secondary:hover { background: var(--surface-3); border-color: var(--hairline-tertiary); }
    .others { margin-top: 28px; }
    .others h2 { margin-bottom: 8px; font-size: 13px; font-weight: 500; letter-spacing: 0.4px; color: var(--ink-subtle); }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      min-height: 36px;
      padding: 0 14px;
      background: var(--surface-1);
      border: 1px solid var(--hairline-strong);
      border-radius: 9999px;
      color: var(--ink-muted);
      font-family: inherit;
      font-size: 14px;
      cursor: pointer;
    }
    .chip:hover { border-color: var(--hairline-tertiary); color: var(--ink); }
  </style>
</head>
<body>
  <a class="topbar" href="/admin" aria-label="E-ink Status Board">
    <img src="/favicon.svg" width="20" height="20" alt="">
    <span>E-ink Status Board</span>
  </a>
  <main>
    <section class="card" aria-live="polite">
      <p class="eyebrow" id="eyebrow">상태 제안</p>
      <h1 id="title">불러오는 중...</h1>
      <p class="sub" id="sub" hidden></p>
      <div class="bezel" id="preview" hidden>
        <div class="screen"><img id="preview-img" alt=""></div>
      </div>
      <p class="error" id="error" hidden></p>
      <div class="actions" id="ask-actions" hidden>
        <button type="button" class="btn btn-primary" id="apply-btn">바꾸기</button>
        <a class="btn btn-secondary" href="/admin">그대로 두기</a>
      </div>
      <div class="actions" id="done-actions" hidden>
        <a class="btn btn-secondary" href="/admin">Admin 열기</a>
      </div>
    </section>
    <section class="others" id="others" hidden>
      <h2 id="others-title">다른 프리셋으로</h2>
      <div class="chips" id="chips"></div>
    </section>
  </main>

  <script>
    const params = new URLSearchParams(location.search);
    const wantedName = (params.get('preset') || '').trim();
    const untilRaw = (params.get('until') || '').trim();
    const until = parseUntil(untilRaw);
    const timeFmt = new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' });

    let presets = [];
    let status = null;
    let target = null;          // 지금 묻고 있는 프리셋

    /** 서버와 같은 규칙: 7자리 소수 초는 버리고, 오프셋이 없으면 UTC */
    function parseUntil(s) {
      const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)(?:\.\d{1,7})?(Z|[+-]\d{2}:\d{2})?$/.exec(s);
      if (!m) return null;
      const t = Date.parse(m[1] + (m[2] || 'Z'));
      return Number.isNaN(t) ? null : new Date(t);
    }

    /** 받침이 있으면 "으로", 없거나 ㄹ 받침이면 "로" */
    function ro(word) {
      const code = word.charCodeAt(word.length - 1) - 0xAC00;
      if (!(code >= 0 && code <= 11171)) return '(으)로';
      const jong = code % 28;
      return jong === 0 || jong === 8 ? '로' : '으로';
    }

    function setText(id, text) {
      const el = document.getElementById(id);
      el.textContent = text;
      el.hidden = !text;
    }

    function show(id, visible) {
      document.getElementById(id).hidden = !visible;
    }

    /** 세션이 끝났으면 로그인한 뒤 이 화면으로 돌아오게 보낸다 */
    function toLogin() {
      location.href = '/?next=' + encodeURIComponent(location.pathname + location.search);
    }

    class LoginRequired extends Error {}

    async function getJson(url) {
      const res = await fetch(url);
      if (res.status === 401) { toLogin(); throw new LoginRequired(); }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }

    /** 복귀 안내 — 서버의 기준(연속 일정이면 처음 상태)과 같은 대상을 보여준다 */
    function revertSentence(preset) {
      if (!until) return '일정 종료 시각이 없어 자동으로 돌아가지 않습니다.';
      const pending = status.pending_revert;
      const baseId = pending ? pending.revert_to_id : status.active_preset_id;
      const baseName = pending ? pending.revert_to_name : status.text;
      if (!baseId || !baseName) return '돌아갈 이전 상태가 없어 그대로 유지됩니다.';
      if (baseId === preset.id) return '';
      return `${timeFmt.format(until)}(일정 종료)에 지금의 '${baseName}'${ro(baseName)} 돌아갑니다.`;
    }

    function renderChips(excludeId, title) {
      const list = presets.filter(p => p.id !== excludeId);
      document.getElementById('chips').replaceChildren(...list.map(p => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = p.name;
        chip.addEventListener('click', () => { renderQuestion(p, '직접 고른 프리셋'); window.scrollTo(0, 0); });
        return chip;
      }));
      setText('others-title', title);
      show('others', list.length > 0);
    }

    function renderQuestion(preset, eyebrow) {
      target = preset;
      setText('eyebrow', eyebrow || '상태 제안');
      setText('title', `'${preset.name}'${ro(preset.name)} 바꾸시겠어요?`);
      setText('sub', revertSentence(preset));
      const img = document.getElementById('preview-img');
      img.src = `/api/presets/${encodeURIComponent(preset.id)}/preview.png`;
      img.alt = preset.name;
      setText('error', '');
      show('preview', true);
      show('ask-actions', true);
      show('done-actions', false);
      renderChips(preset.id, '다른 프리셋으로');
    }

    function renderMessage(eyebrow, title, sub) {
      setText('eyebrow', eyebrow);
      setText('title', title);
      setText('sub', sub);
      show('preview', false);
      show('ask-actions', false);
      show('done-actions', true);
    }

    function renderDone(preset, result) {
      const parts = [];
      if (result.deferred) parts.push('잠시 후 화면에 반영됩니다.');
      if (result.revert) {
        const name = result.revert.revert_to_name || '이전 상태';
        parts.push(`${timeFmt.format(new Date(result.revert.at))}에 '${name}'${ro(name)} 돌아갑니다.`);
      }
      renderMessage('적용됨', `'${preset.name}'${ro(preset.name)} 바꿨습니다`, parts.join(' '));
      show('preview', true);
      show('others', false);
    }

    async function applyTarget(e) {
      const btn = e.currentTarget;
      btn.disabled = true;
      setText('error', '');
      try {
        const res = await fetch('/api/suggestions/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset_id: target.id, until: untilRaw || null }),
        });
        if (res.status === 401) { toLogin(); return; }
        let body = {};
        try { body = await res.json(); } catch { /* 본문 없음 */ }
        if (res.status === 409) {
          renderMessage('상태 제안', '이미 끝난 일정입니다', '이 제안은 더 이상 적용하지 않습니다.');
          show('others', false);
          return;
        }
        if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`);
        renderDone(target, body);
      } catch (err) {
        setText('error', `바꾸지 못했습니다: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
    }

    async function init() {
      document.getElementById('apply-btn').addEventListener('click', applyTarget);

      if (until && until <= new Date()) {
        renderMessage('상태 제안', '이미 끝난 일정입니다', '이 제안은 더 이상 적용하지 않습니다.');
        return;
      }
      try {
        [presets, status] = await Promise.all([getJson('/api/presets'), getJson('/status')]);
      } catch (err) {
        if (!(err instanceof LoginRequired)) {
          renderMessage('상태 제안', '불러오지 못했습니다', '잠시 후 링크를 다시 열어 주세요.');
        }
        return;
      }

      const found = presets.find(p => p.name === wantedName);
      if (!found) {
        renderMessage('상태 제안',
          wantedName ? `'${wantedName}' 프리셋이 없습니다` : '바꿀 프리셋을 고르세요',
          wantedName ? '이름이 바뀌었거나 삭제됐을 수 있습니다. 아래에서 골라 주세요.' : '');
        renderChips(null, '프리셋 목록');
        return;
      }
      if (found.id === status.active_preset_id) {
        renderMessage('상태 제안', `이미 '${found.name}' 상태입니다`, '바꿀 필요가 없습니다.');
        renderChips(found.id, '다른 프리셋으로');
        return;
      }
      renderQuestion(found);
    }

    init();
  </script>
</body>
</html>
```

- [ ] **Step 5: 게이트 테스트 통과 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_auth.py 2>&1 | grep -E "^SUMMARY|FAIL"`
Expected: `SUMMARY t_auth: … 0 failed`

- [ ] **Step 6: 브라우저 검사 (Review Focus 1, 5 포함)**

로컬 서버를 띄우고 프리셋을 만든다:

```bash
S=/private/tmp/claude-501/-Users-junja-Desktop-claude-eink-status-board/0907953a-8467-4470-8ffb-6f6aeb437501/scratchpad/run/sg
PY=/Users/junja/Desktop/claude/eink-status-board/.omc/scratch/venv/bin/python
mkdir -p $S/data/images && cp /Users/junja/Desktop/claude/eink-status-board/server/main.py $S/ && cp -R /Users/junja/Desktop/claude/eink-status-board/server/static $S/
cd $S && (env AUTH_DISABLED=true SERVER_HOST=127.0.0.1 SERVER_PORT=5105 MIN_PUSH_INTERVAL=0 $PY main.py > $S/server.log 2>&1 &)
for i in $(seq 1 30); do curl -s localhost:5105/healthz >/dev/null && break; sleep 0.3; done
for n in "근무 중" "회의 중" "점심 식사" "외근 중"; do curl -s -X POST localhost:5105/api/presets/text -H 'Content-Type: application/json' -d "{\"name\":\"$n\"}" >/dev/null; done
A=$(curl -s localhost:5105/api/presets | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])"); curl -s -X POST localhost:5105/api/presets/$A/activate >/dev/null
```

chrome-devtools(격리 컨텍스트, 390x844 모바일과 1280x900 데스크톱)로 다음 주소를 열어 스크린샷과 `evaluate_script` 로 확인한다. `U` 는 지금+1시간의 ISO 시각(`new Date(Date.now()+3600e3).toISOString()`).

| # | 주소 | 기대 |
|---|------|------|
| 4-1 | `/suggest?preset=%ED%9A%8C%EC%9D%98%20%EC%A4%91&until=<U>` | 제목 "'회의 중'으로 바꾸시겠어요?", 부제에 "(일정 종료)에 지금의 '근무 중'으로 돌아갑니다", 미리보기, [바꾸기]/[그대로 두기], 다른 프리셋 칩 3개 |
| 4-2 | 4-1 에서 [바꾸기] | 제목 "'회의 중'으로 바꿨습니다", "…에 '근무 중'으로 돌아갑니다", 칩 숨김. `/status` 의 `pending_revert.revert_to_name == "근무 중"` |
| 4-3 | 같은 주소 다시 열기 | "이미 '회의 중' 상태입니다" + 다른 프리셋 칩 |
| 4-4 | `/suggest?preset=없는이름` | "'없는이름' 프리셋이 없습니다" + 프리셋 목록 칩 4개, 칩을 누르면 질문 화면 |
| 4-5 | `/suggest?preset=%ED%9A%8C%EC%9D%98%20%EC%A4%91&until=2020-01-01T00:00:00Z` | "이미 끝난 일정입니다", [바꾸기] 없음 |
| 4-6 | `/suggest?preset=%EC%A0%90%EC%8B%AC+%EC%8B%9D%EC%82%AC` (`+` 공백) | "'점심 식사'로 바꾸시겠어요?" (받침 없음 → "로") |
| 4-7 | 4-6 화면에서 `evaluate_script`: `window.fetch = async () => new Response('{"detail":"x"}', {status: 401}); document.getElementById('apply-btn').click(); return true;` 실행 후 1초 기다렸다가 `list_network_requests` | 문서 요청 목록에 `GET /?next=%2Fsuggest%3Fpreset%3D%25EC%25A0%2590…` 이 있다 (무인증 모드라 그 뒤 다른 화면으로 다시 리디렉션되는 것은 정상) |
| 4-8 | 모바일 390px | `document.documentElement.scrollWidth === 390` (가로 스크롤 없음) |

확인 후 서버를 끈다: `kill $(lsof -tiTCP:5105 -sTCP:LISTEN)`

- [ ] **Step 7: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/main.py server/static/suggest.html
git commit -m "feat(suggest): confirmation page for calendar suggestions"
```

---

### Task 5: 로그인 후 원래 링크로 돌아가기 (`next`)

**Files:**
- Modify: `server/main.py` — `auth_gate` 리디렉션, `root()`, `login()`, `auth_callback()`
- Modify: `server/static/login.html` — `</main>` 뒤에 스크립트
- Test: `.omc/scratch/tests/t_auth.py`

**Interfaces:**
- Consumes: `_safe_next`, `quote` (Task 1), `suggest.html` 의 `toLogin()` (Task 4)
- Produces: 미인증 브라우저 `GET /suggest…` → `303 /?next=<인코딩된 주소>`; `/login?next=` → `session["next"]`; 콜백 성공 → `303 <next>` 또는 `/admin`

- [ ] **Step 1: 실패하는 테스트 — `t_auth.py`**

B 스윕의 기대값

```python
    if not (r.status_code == 303 and r.headers.get("location") == "/"):
        bad.append(f"GET text/html -> {r.status_code} loc={r.headers.get('location')}")
    check(G, f"{path}: all methods + HEAD/OPTIONS -> 401 JSON, html GET -> 303 /", not bad, "; ".join(bad))
```
을
```python
    want = "/?next=%2Fsuggest" if path == "/suggest" else "/"
    if not (r.status_code == 303 and r.headers.get("location") == want):
        bad.append(f"GET text/html -> {r.status_code} loc={r.headers.get('location')}")
    check(G, f"{path}: all methods + HEAD/OPTIONS -> 401 JSON, html GET -> 303 {want}", not bad, "; ".join(bad))
```
로 바꾼다.

Task 4 에서 넣은 `S-suggest-auth` 블록 바로 아래(`# … H` 줄 위)에 추가:

```python
G = "S-next"
link = "/suggest?preset=%ED%9A%8C%EC%9D%98&until=2026-10-10T05%3A00%3A00Z"
r = rq("GET", link, headers={"Accept": "text/html"})
check(G, "browser GET /suggest… without a session -> 303 /?next=<link>",
      r.status_code == 303 and r.headers.get("location") == "/?next=" + urllib.parse.quote(link, safe=""), r.headers.get("location"))
r = rq("GET", "/admin", headers={"Accept": "text/html"})
check(G, "browser GET /admin without a session -> 303 / (no next)", r.headers.get("location") == "/", r.headers.get("location"))
r = rq("GET", "/?next=" + urllib.parse.quote("/suggest?preset=x", safe=""), headers=OK_COOKIE)
check(G, "logged-in / with next -> 303 to that link", r.status_code == 303 and r.headers.get("location") == "/suggest?preset=x", r.headers.get("location"))
for bad in ("//evil.example", "https://evil.example", "/admin@evil.example", "/\\evil.example", "/status"):
    r = rq("GET", "/?next=" + urllib.parse.quote(bad, safe=""), headers=OK_COOKIE)
    check(G, f"logged-in / with next={bad!r} -> /admin", r.headers.get("location") == "/admin", r.headers.get("location"))
r = rq("GET", "/?next=" + urllib.parse.quote("/suggest?preset=x", safe=""))
check(G, "logged-out / with next -> landing page that forwards next", r.status_code == 200 and "/login?next=" in r.text, str(r.status_code))

from starlette.responses import RedirectResponse as _Redirect
_orig_redirect = client.authorize_redirect


async def fake_redirect(request, redirect_uri, **kw):
    request.session["_state_google_fake"] = {"data": {}}
    return _Redirect("https://accounts.google.com/o/oauth2/v2/auth?fake=1", status_code=302)


client.authorize_redirect = fake_redirect
r = rq("GET", "/login?next=" + urllib.parse.quote("/suggest?preset=x", safe=""))
sess, _ = session_after(r)
check(G, "/login?next=<allowed> remembers it in the session", r.status_code == 302 and bool(sess) and sess.get("next") == "/suggest?preset=x", str(sess))
r = rq("GET", "/login?next=" + urllib.parse.quote("//evil.example", safe=""),
       headers={"Cookie": f"eink_session={mint_cookie({'next': '/suggest?preset=old'}, gen=None)}"})
sess, _ = session_after(r)
check(G, "/login?next=<disallowed> forgets an earlier next", sess is not None and "next" not in sess, str(sess))
r = rq("GET", "/login?next=" + urllib.parse.quote("/suggest?preset=x", safe=""), headers=OK_COOKIE)
check(G, "/login while logged in -> straight to next", r.status_code == 303 and r.headers.get("location") == "/suggest?preset=x", r.headers.get("location"))
client.authorize_redirect = _orig_redirect

STATE["mode"] = {"userinfo": {"email": ALLOWED, "email_verified": True}}
r = rq("GET", "/auth/callback?code=c&state=s", headers={"Cookie": f"eink_session={mint_cookie({'next': '/suggest?preset=x'}, gen=None)}"})
sess, _ = session_after(r)
check(G, "callback success -> 303 to the remembered link, next not kept",
      r.status_code == 303 and r.headers.get("location") == "/suggest?preset=x" and bool(sess) and "next" not in sess
      and sess.get("email") == ALLOWED, f"{r.headers.get('location')} {sess}")
for bad in ("//evil.example", "https://evil.example/x", "/status", 5):
    r = rq("GET", "/auth/callback?code=c&state=s", headers={"Cookie": f"eink_session={mint_cookie({'next': bad}, gen=None)}"})
    check(G, f"callback with tampered next={bad!r} -> /admin", r.headers.get("location") == "/admin", r.headers.get("location"))
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests && ../venv/bin/python t_auth.py 2>&1 | grep -E "FAIL \[(S-next|B-gate)\]" | head -5`
Expected: `FAIL [B-gate] /suggest: … html GET -> 303 /?next=%2Fsuggest` 와 `FAIL [S-next] …` 여러 줄

- [ ] **Step 3: 구현 — `auth_gate`**

```python
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/", status_code=303)   # 브라우저는 로그인 전 메인 페이지로
```
를
```python
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                # 브라우저는 로그인 전 메인 페이지로. 일정 제안 링크는 로그인 후 그 화면으로 돌아오게 주소를 넘긴다
                query = request.scope["query_string"].decode("latin-1")
                nxt = _safe_next(path + ("?" + query if query else "")) if path == "/suggest" else None
                return RedirectResponse("/?next=" + quote(nxt, safe="") if nxt else "/", status_code=303)
```
로 바꾼다.

- [ ] **Step 4: 구현 — `root()`, `login()`, `auth_callback()`**

`root()` 의

```python
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse(url="/admin", status_code=303)
```
를
```python
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse(url=_safe_next(request.query_params.get("next")) or "/admin", status_code=303)
```
로 바꾼다.

`login()` 의

```python
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse("/admin", status_code=303)

    # (완료되지 않은 이전 시도의 state 는 Authlib 이 새 state 를 저장할 때 스스로 정리한다)
```
를
```python
    nxt = _safe_next(request.query_params.get("next"))
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse(nxt or "/admin", status_code=303)

    # 로그인 후 돌아갈 주소 (허용 목록에 맞을 때만, 콜백이 다시 검사한다)
    if nxt:
        request.session["next"] = nxt
    else:
        request.session.pop("next", None)

    # (완료되지 않은 이전 시도의 state 는 Authlib 이 새 state 를 저장할 때 스스로 정리한다)
```
로 바꾼다.

`auth_callback()` 의

```python
    # Google 로그인을 실제로 마친 경우에만 여기 도달한다(state 검증 통과) — 교차 사이트로는 유발할 수 없다
    request.session.clear()
```
를
```python
    # Google 로그인을 실제로 마친 경우에만 여기 도달한다(state 검증 통과) — 교차 사이트로는 유발할 수 없다
    nxt = _safe_next(request.session.get("next"))   # clear() 전에 꺼내 둔다
    request.session.clear()
```
로, 같은 함수 끝의

```python
    logger.info(f"🔑 로그인: {email}")
    return RedirectResponse("/admin", status_code=303)
```
를
```python
    logger.info(f"🔑 로그인: {email}")
    return RedirectResponse(nxt or "/admin", status_code=303)
```
로 바꾼다.

- [ ] **Step 5: 구현 — `login.html` 스크립트**

`</main>` 와 `</body>` 사이에 추가:

```html
  <script>
    // 로그인 후 돌아갈 주소를 /login 으로 넘긴다 (서버가 같은 규칙으로 다시 검사한다)
    (function () {
      var next = new URLSearchParams(location.search).get('next');
      if (next && next.length <= 1024 && /^\/(?:admin|suggest)(?:\?[^#\\\s]*)?$/.test(next)) {
        document.querySelector('a.btn').href = '/login?next=' + encodeURIComponent(next);
      }
    })();
  </script>
```

- [ ] **Step 6: 통과 확인 (3.14·3.10)과 인증 회귀**

Run:
```bash
cd /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests
../venv/bin/python t_auth.py 2>&1 | grep -E "^SUMMARY|FAIL"
TEST_PY=../venv310/bin/python ../venv310/bin/python t_auth.py 2>&1 | grep -E "^SUMMARY|FAIL"
../venv/bin/python t_auth_nokeys.py 2>&1 | grep "^SUMMARY"
```
Expected: 모두 `0 failed`

- [ ] **Step 7: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/main.py server/static/login.html
git commit -m "feat(auth): return to the suggestion link after Google login"
```

---

### Task 6: Admin 의 복귀 예약 표시와 [취소]

**Files:**
- Modify: `server/static/admin.html` — CSS(`.preview-label` 규칙 아래), 마크업(`#current-status-label` 아래), JS(`fetchStatus`, `DOMContentLoaded`, `handleForceRefresh` 아래)

**Interfaces:**
- Consumes: `/status.pending_revert` (`{at, revert_to_id, revert_to_name} | null`), `DELETE /api/suggestions/revert`

- [ ] **Step 1: 마크업**

```html
        <div class="preview-label" id="current-status-label">표시 중인 내용 없음</div>
```
아래에 추가:
```html
        <div class="revert-note" id="revert-note" hidden>
          <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
          <span id="revert-text"></span>
          <button type="button" class="btn btn-ghost btn-sm" id="revert-cancel">취소</button>
        </div>
```

- [ ] **Step 2: CSS** — `.preview-label { … }` 규칙 바로 아래에 추가

```css
    .revert-note {
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
      gap: 6px 8px;
      margin-top: 8px;
      font-size: 13px;
      color: var(--ink-subtle);
    }
```

- [ ] **Step 3: JS**

`fetchStatus()` 의 `renderCurrentDisplay(data);` 바로 아래에 추가:
```js
        renderRevert(data.pending_revert ?? null);
```

`DOMContentLoaded` 핸들러의 `document.getElementById('refresh-btn').addEventListener('click', handleForceRefresh);` 아래에 추가:
```js
      document.getElementById('revert-cancel').addEventListener('click', handleCancelRevert);
```

`handleForceRefresh` 함수 바로 아래에 추가:
```js
    const revertTimeFmt = new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' });

    /** 받침이 있으면 "으로", 없거나 ㄹ 받침이면 "로" */
    function josaRo(word) {
      const code = word.charCodeAt(word.length - 1) - 0xAC00;
      if (!(code >= 0 && code <= 11171)) return '(으)로';
      const jong = code % 28;
      return jong === 0 || jong === 8 ? '로' : '으로';
    }

    /** 일정 제안으로 바꾼 화면이 언제 무엇으로 돌아가는지 보여준다 */
    function renderRevert(info) {
      const box = document.getElementById('revert-note');
      if (!info) { box.hidden = true; return; }
      const name = info.revert_to_name || '이전 상태';
      document.getElementById('revert-text').textContent =
        `${revertTimeFmt.format(new Date(info.at))}에 '${name}'${josaRo(name)} 돌아갑니다`;
      box.hidden = false;
    }

    async function handleCancelRevert(e) {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await apiFetch('/api/suggestions/revert', { method: 'DELETE' });
        if (!res.ok) throw new Error(await errorDetail(res, '취소 실패'));
        showToast('복귀 예약을 취소했습니다', 'info');
        fetchStatus();
      } catch (err) {
        if (!(err instanceof AuthRedirect)) showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    }
```

- [ ] **Step 4: 브라우저 검사**

Task 4 Step 6 과 같은 방법으로 서버(5105)를 띄우고, `POST /api/suggestions/apply` 로 `until=<지금+1시간>` 예약을 만든 뒤 `/admin` 을 연다.
기대: 미리보기 아래 "오후 N:NN에 '근무 중'으로 돌아갑니다 [취소]". [취소] → 토스트 "복귀 예약을 취소했습니다", 줄이 사라지고 `/status.pending_revert == null`.
데스크톱(1280)·모바일(390) 스크린샷으로 줄바꿈·가로 스크롤 없음 확인. 끝나면 서버를 끈다.

- [ ] **Step 5: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add server/static/admin.html
git commit -m "feat(admin): show and cancel the pending revert"
```

---

### Task 7: 문서

**Files:**
- Create: `docs/power-automate.md`
- Modify: `README.md`, `CLAUDE.md`, `docs/google-oauth.md`

- [ ] **Step 1: `docs/power-automate.md` 작성**

````markdown
# Power Automate 로 일정 제안 받기

회사 Outlook 일정이 시작하기 5분 전에 Teams 로 **"'회의 중'으로 바꾸시겠어요?"** 메시지를 받고,
링크를 열어 **[바꾸기]** 한 번이면 전자잉크 화면이 바뀝니다. 일정이 끝나면 서버가 바꾸기 전 상태로 되돌립니다
(그 사이 직접 다른 상태로 바꿨다면 건드리지 않습니다).

```
Outlook 일정 ─(시작 5분 전)→ Power Automate ─→ Teams 메시지 ─(링크)→ https://<도메인>/suggest ─[바꾸기]→ 화면 변경
                                                                                     └ 일정 종료 시각 → 이전 상태로 복귀
```

- 서버는 일정을 읽지 않습니다. 회사 안(Power Automate)에서 일정 제목으로 프리셋을 고르고, 링크에는 **프리셋 이름과 종료 시각만** 들어갑니다. 일정 제목은 Teams 메시지 안에만 남습니다.
- 쓰는 커넥터는 Office 365 Outlook·Microsoft Teams 두 개로 모두 **Standard** 입니다 (프리미엄 HTTP 커넥터 불필요).
- 링크를 열어도 아무것도 바뀌지 않습니다. [바꾸기] 를 눌러야 바뀌고, Google 로그인이 필요합니다.

## 목차

1. [흐름 A — 일정 시작 전 제안](#흐름-a--일정-시작-전-제안)
2. [흐름 B — 시간대 제안 (선택)](#흐름-b--시간대-제안-선택)
3. [Teams 가 막혀 있을 때 — 메일로 받기](#teams-가-막혀-있을-때--메일로-받기)
4. [키워드 바꾸기](#키워드-바꾸기)
5. [테스트](#테스트)
6. [문제 해결](#문제-해결)

## 흐름 A — 일정 시작 전 제안

[make.powerautomate.com](https://make.powerautomate.com) → **만들기** → **자동화된 클라우드 흐름**.

| # | 동작 | 설정 |
|---|------|------|
| 1 | 트리거 **When an upcoming event is starting soon (V3)** (Office 365 Outlook) | Calendar Id: `Calendar`(기본 일정) · Look-Ahead Time: `5` |
| 2 | **Filter array** (데이터 작업) | From: `@{triggerOutputs()?['body/value']}` · 고급 모드 식: 아래 ① |
| 3 | **Apply to each** | 출력 선택: `@{body('Filter_array')}` |
| 4 | (3 안) **Compose** — 이름을 `Preset` 으로 | 입력: 아래 ② |
| 5 | (3 안) **Compose** — 이름을 `Link` 로 | 입력: 아래 ③ |
| 6 | (3 안) **Post message in a chat or channel** (Microsoft Teams) | Post as: `Flow bot` · Post in: `Chat with Flow bot` · Recipient: 내 회사 이메일 · Message: 아래 ④ |

① 제외 조건 — 종일 일정, "약속 없음(free)", 거절한 일정, 취소된 일정:

```
@and(equals(item()?['isAllDay'], false), not(equals(item()?['showAs'], 'free')), not(equals(item()?['responseType'], 'declined')), not(startsWith(coalesce(item()?['subject'], ''), 'Canceled:')), not(startsWith(coalesce(item()?['subject'], ''), '취소됨:')))
```

② 제목 키워드 → 프리셋 이름 (Admin 에 있는 이름과 **글자까지 같아야** 합니다):

```
if(contains(toLower(coalesce(item()?['subject'], '')), '점심'), '점심 식사', if(contains(toLower(coalesce(item()?['subject'], '')), '외근'), '외근 중', '회의 중'))
```

③ 링크 — `<도메인>` 을 내 서버 주소로 바꿉니다. 종료 시각은 반드시 **`endWithTimeZone`** (오프셋이 붙은 값)을 씁니다:

```
concat('https://<도메인>/suggest?preset=', encodeUriComponent(outputs('Preset')), '&until=', encodeUriComponent(item()?['endWithTimeZone']))
```

④ Teams 메시지 (코드 보기로 붙여넣기):

```html
<p><b>@{formatDateTime(convertFromUtc(item()?['start'], 'Korea Standard Time'), 'HH:mm')} @{item()?['subject']}</b></p>
<p>'@{outputs('Preset')}'(으)로 바꾸시겠어요?</p>
<p><a href="@{outputs('Link')}">바꾸기 화면 열기</a></p>
```

## 흐름 B — 시간대 제안 (선택)

점심처럼 일정에 없는 반복 시간대용입니다. **예약된 클라우드 흐름**으로 만듭니다.

| # | 동작 | 설정 |
|---|------|------|
| 1 | 트리거 **Recurrence** | 간격 1 · 빈도 주 · 표준 시간대 `(UTC+09:00) Seoul` · 요일 월~금 · 시간 `11` · 분 `55` |
| 2 | **Compose** — 이름 `Until` | `convertToUtc(concat(formatDateTime(convertFromUtc(utcNow(), 'Korea Standard Time'), 'yyyy-MM-dd'), 'T13:00:00'), 'Korea Standard Time')` |
| 3 | **Compose** — 이름 `Link` | `concat('https://<도메인>/suggest?preset=', encodeUriComponent('점심 식사'), '&until=', encodeUriComponent(outputs('Until')))` |
| 4 | **Post message in a chat or channel** | 흐름 A 의 6번과 같게, 메시지: `<p>점심시간이에요. '점심 식사'로 바꾸시겠어요?</p><p><a href="@{outputs('Link')}">바꾸기 화면 열기</a></p>` |

## Teams 가 막혀 있을 때 — 메일로 받기

회사 Teams 관리 센터에서 **Workflows** 앱(Flow bot)을 막아 두었으면 6번 동작이 실패합니다.
대신 Office 365 Outlook **Send an email (V2)** 로 나에게 같은 내용을 보냅니다.

| 항목 | 값 |
|------|----|
| To | 내 회사 이메일 |
| Subject | `'@{outputs('Preset')}'(으)로 바꾸시겠어요? — @{item()?['subject']}` |
| Body | 흐름 A 의 ④ |

## 키워드 바꾸기

흐름 A 의 ② 식만 고치면 됩니다. `if(contains(…, '키워드'), '프리셋 이름', <나머지>)` 를 앞에 하나씩 덧붙입니다.
마지막 `'회의 중'` 이 어느 키워드에도 맞지 않는 일정의 기본값입니다.
프리셋 이름이 Admin 과 다르면 제안 화면이 "프리셋이 없습니다" 와 함께 전체 목록을 보여줍니다.

## 테스트

1. Outlook 에 **지금부터 7분 뒤** 시작하는 테스트 일정을 만듭니다 (제목에 키워드, 길이 10분).
2. 2분쯤 뒤 Teams 에 메시지가 오는지 봅니다. 흐름 실행 기록(내 흐름 → 흐름 → 실행 기록)에서 각 단계 결과를 볼 수 있습니다.
3. 링크 → [바꾸기] → 패널이 바뀌고, Admin 의 현재 디스플레이 아래에 "…에 '근무 중'으로 돌아갑니다" 가 보입니다.
4. 일정 종료 시각에 이전 상태로 돌아오는지 확인하고 테스트 일정을 지웁니다.

## 문제 해결

| 증상 | 원인과 해결 |
|------|-------------|
| 메시지가 안 옴 | 실행 기록이 없으면 트리거가 안 돈 것 — Calendar Id 가 기본 일정인지 확인. Microsoft 는 드물게 트리거가 최대 1시간 늦을 수 있다고 안내합니다 |
| 6번 동작 실패 `BotNotInConversationRoster` 등 | Flow bot(Workflows 앱) 차단 — [메일로 받기](#teams-가-막혀-있을-때--메일로-받기) |
| 링크를 열면 로그인 화면 | Teams 가 링크를 앱 안의 브라우저로 열면 Chrome 의 로그인 상태를 쓰지 못합니다. 한 번 로그인하면 원래 제안 화면으로 돌아오고, 그 브라우저에는 30일간 유지됩니다. 로그인이 막히면 "브라우저에서 열기" 로 Chrome 에서 여세요 |
| "프리셋이 없습니다" | ② 의 이름과 Admin 의 프리셋 이름이 다름 (띄어쓰기 포함) |
| 복귀 시각이 9시간 어긋남 | 링크에 `end`/`start` 같은 오프셋 없는 값을 넣었거나 KST 를 UTC 로 착각 — ③ 처럼 `endWithTimeZone` 을 쓰세요 |
| "이미 끝난 일정입니다" | 일정 종료 뒤에 링크를 열었음 — 의도된 동작 |
| 일정 종료 후 돌아오지 않음 | 그 사이 직접 다른 상태로 바꿨거나, Admin 에서 [취소] 했거나, 되돌릴 프리셋을 지웠음 |
````

- [ ] **Step 2: `README.md`** — 찾기 → 바꾸기 7곳

① 소개 문단

찾기:
```
iPhone 단축어·브라우저·curl 어디서든 한 번에 전자잉크 화면을 바꿉니다.
```
바꾸기:
```
iPhone 단축어·브라우저·curl 어디서든 한 번에 전자잉크 화면을 바꿉니다.
회사 Outlook 일정에 맞춰 Teams 로 "'회의 중'으로 바꾸시겠어요?" 제안을 받고, 일정이 끝나면 이전 상태로 돌아가게 할 수도 있습니다 ([Power Automate 연동](docs/power-automate.md)).
```

② 인증 실패 설명

찾기:
```
- 인증에 실패하면 `Accept` 에 `text/html` 이 있는 GET(브라우저 주소창)은 `303` 으로 로그인 전 메인 페이지(`/`)에 보내고,
```
바꾸기:
```
- 인증에 실패하면 `Accept` 에 `text/html` 이 있는 GET(브라우저 주소창)은 `303` 으로 로그인 전 메인 페이지(`/`, `/suggest…` 는 `/?next=<그 주소>`)에 보내고,
```

③ 로그인·페이지 표

찾기:
```
| `GET` | `/` | 없음 | 로그인 전 메인 페이지(`static/login.html`, "Google 계정으로 로그인" 버튼 → `/login`). 이미 로그인했으면 `303` → `/admin` |
| `GET` | `/admin` | 세션 | 관리자 페이지 |
```
바꾸기:
```
| `GET` | `/` | 없음 | 로그인 전 메인 페이지(`static/login.html`, "Google 계정으로 로그인" 버튼 → `/login`). 이미 로그인했으면 `303` → `/admin` (`?next=` 가 허용된 주소 `/admin`·`/suggest…` 면 그리로). `next` 는 `/login?next=` → 로그인 후 콜백이 그 주소로 보냄 |
| `GET` | `/admin` | 세션 | 관리자 페이지 |
| `GET` | `/suggest` | 세션 | 일정 제안 확인 화면 (`?preset=<이름>&until=<종료 시각>`). 열기만 해서는 아무것도 바뀌지 않음 |
```

④ `/status` 행 — 찾기:
```
`devices[]`, `auth_enabled`, `user`(로그인 이메일) |
```
바꾸기:
```
`devices[]`, `auth_enabled`, `user`(로그인 이메일), `pending_revert`(일정 종료 복귀 예약 `{at, revert_to_id, revert_to_name}` 또는 `null`) |
```

⑤ 새 절 — 찾기:
```
| `connected_at`, `last_seen` | ISO 8601 UTC 시각 |

### 프리셋
```
바꾸기:
```
| `connected_at`, `last_seen` | ISO 8601 UTC 시각 |

### 일정 제안

| Method | Endpoint | 인증 | 설명 |
|--------|----------|------|------|
| `POST` | `/api/suggestions/apply` | 세션 | 본문 `{"preset_id": "<8자리 hex>", "until": "<ISO 8601>" \| null}`. 프리셋을 적용하고 `until` 에 바꾸기 직전 프리셋으로 돌아가도록 예약. 응답은 활성화 결과 + `revert`(예약 또는 `null`). `until` 이 지났으면 `409`(적용 안 함), 없거나 형식이 틀리거나 24시간 넘게 남았으면 적용만 함. 연속 일정이면 처음 상태를 복귀 대상으로 유지 |
| `DELETE` | `/api/suggestions/revert` | 세션 | 복귀 예약 취소 → `{"cancelled": true \| false}` |

- `until` 은 `Z`·`+09:00` 같은 오프셋을 붙이거나, 없으면 UTC 로 읽습니다. 7자리 소수 초(Power Automate 형식)도 받습니다.
- 복귀는 그 시각에 **제안한 프리셋이 아직 표시 중일 때만** 합니다. 그 사이 다른 프리셋을 직접 적용하면 예약이 바로 사라집니다. 되돌릴 프리셋이나 제안한 프리셋을 지워도 사라집니다.
- 예약은 하나뿐이며 `state.json` 에 저장돼 서버를 재시작해도 이어집니다. 재시작 중에 시각이 지났으면 시작 직후 처리합니다.

### 프리셋
```

⑥ 상태 저장 표 — 찾기:
```
`devices`(디바이스 접속 이력) |
```
바꾸기:
```
`devices`(디바이스 접속 이력), `pending_revert`(일정 종료 복귀 예약: 되돌릴 프리셋·제안한 프리셋·시각) |
```

⑦ 문서 목록 — 찾기:
```
- [Apple 단축어 연동](docs/apple-shortcuts.md) — 마스터 단축어 만들기, API 키, 문제 해결
```
바꾸기:
```
- [Apple 단축어 연동](docs/apple-shortcuts.md) — 마스터 단축어 만들기, API 키, 문제 해결
- [Power Automate 연동](docs/power-automate.md) — Outlook 일정 → Teams 제안 메시지, 시간대 제안, 문제 해결
```

- [ ] **Step 3: `CLAUDE.md`** — 찾기 → 바꾸기 5곳

① Server 절 — 찾기:
```
- **Apple 단축어 연동**: `/api/shortcuts/names`가 plain text 줄바꿈 목록 반환
```
바꾸기 (두 항목을 앞에 추가하고 원래 줄은 그대로 이어 둔다):
```
- **일정 제안·복귀 예약**: Power Automate(회사)가 Teams 로 `/suggest?preset=<이름>&until=<종료 시각>` 링크를 보낸다(서버는 일정을 읽지 않는다 — `docs/power-automate.md`). `GET /suggest`(화면만, `static/suggest.html`) → `POST /api/suggestions/apply` 가 적용하고 `pending_revert`(`{revert_to, expected, at}`, 하나뿐, state.json 저장)를 건다. 활성화 본체는 `_activate_core(preset, force, expected_current)` — 교체 직전 ID 를 돌려주고, `expected_current` 가 현재와 다르면 아무것도 안 바꾸며, 교체 후 현재가 `expected` 와 다르면 예약을 지운다. 세 판단 모두 `_activate_lock` 안. 복귀 대상은 연속 일정이면 처음 상태(`old.revert_to`)를 유지. 타이머(`_revert_task`)는 lifespan 이 복원 직후 걸고(지난 시각이면 즉시) 종료 시 끈다. `until` 은 `parse_until`(정규식, 오프셋 없으면 UTC, 64자, 24시간 이내만 예약, 지난 시각 409) — `fromisoformat` 으로 바꾸지 말 것(3.10)
- **로그인 후 복귀(`next`)**: 미인증 브라우저 `GET /suggest…` 만 `/?next=<주소>` 로 보낸다. `/` 의 버튼(login.html JS) → `/login?next=` → `session["next"]` → 콜백 성공 시 `session.clear()` 전에 꺼내 그리로 303. 모든 지점에서 `_safe_next`(`NEXT_RE` = `/admin`·`/suggest` 만, 1024자)로 다시 검사 — 허용 목록을 넓히거나 절대 URL 을 받지 말 것(open redirect)
- **Apple 단축어 연동**: `/api/shortcuts/names`가 plain text 줄바꿈 목록 반환
```

② Key Constants — 찾기:
```
| Session | 30일 | `SESSION_MAX_AGE` |
```
바꾸기:
```
| Session | 30일 | `SESSION_MAX_AGE` |
| Suggest | until 64자 / 복귀 예약 24시간 이내 / next 1024자 | `MAX_UNTIL_LENGTH`, `MAX_REVERT_AHEAD`, `MAX_NEXT_LENGTH` |
```

③ Admin UI — 찾기:
```
단축어 가이드(주소는 지금 접속한 `location.origin` 으로 채움 — 운영에서는 항상 https)
```
바꾸기:
```
단축어 가이드(주소는 지금 접속한 `location.origin` 으로 채움 — 운영에서는 항상 https), 일정 제안 복귀 예약 표시와 [취소](`/status.pending_revert`)
```

④ Docs — 찾기:
```
- `docs/apple-shortcuts.md` — 마스터 단축어 구성 절차(`X-API-Key` 헤더 포함)
```
바꾸기:
```
- `docs/apple-shortcuts.md` — 마스터 단축어 구성 절차(`X-API-Key` 헤더 포함)
- `docs/power-automate.md` — Outlook 일정 → Teams 제안 흐름(A/B), 메일 대체 경로, 문제 해결
```

⑤ Gotchas — 찾기:
```
- **requirements**: `starlette>=0.49.1` 은 multipart·FileResponse Range DoS 권고 때문이므로 낮추지 말 것
```
바꾸기:
```
- **requirements**: `starlette>=0.49.1` 은 multipart·FileResponse Range DoS 권고 때문이므로 낮추지 말 것
- **Python 3.10**: 운영 VM 은 Python 3.10.12. 3.11+ 전용 API(`fromisoformat` 의 `Z`·7자리 소수 초, `datetime.UTC`, `asyncio.TaskGroup`)를 쓰지 말 것. 테스트는 `TEST_PY=.omc/scratch/venv310/bin/python` 으로도 돌린다
```

- [ ] **Step 4: `docs/google-oauth.md`** — 찾기:
```
메인 페이지를 바꿔도 Redirect URI(`/auth/callback`)는 달라지지 않습니다.
```
바꾸기:
```
메인 페이지를 바꿔도 Redirect URI(`/auth/callback`)는 달라지지 않습니다. 로그인 전에 일정 제안 링크(`/suggest…`)를 열었다면 그 주소가 `?next=` 로 전달돼, 로그인을 마치면 `/admin` 대신 그 제안 화면으로 돌아갑니다 (`/admin`·`/suggest` 외의 주소는 받지 않습니다).
```

- [ ] **Step 5: 문서 점검**

Run: `cd /Users/junja/Desktop/claude/eink-status-board && grep -rniEf .omc/scratch/private-patterns.txt README.md CLAUDE.md docs/ ; echo "exit=$? (1 = 실제 값 없음)"   # 패턴 파일은 git 제외, 실제 이메일·호스트를 담는다`
Expected: `exit=1`

- [ ] **Step 6: 커밋**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git add docs/power-automate.md README.md CLAUDE.md docs/google-oauth.md
git commit -m "docs: calendar suggestions via Power Automate and Teams"
```

---

### Task 8: 전체 검증과 리뷰

**Files:** 없음 (검증만)

- [ ] **Step 1: 전체 테스트 — 3.14**

Run: `bash /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests/run_all.sh 2>&1 | grep -E "^== |^SUMMARY"`
Expected: 8개 스크립트 모두 `0 failed`

- [ ] **Step 2: 전체 테스트 — 3.10 (운영과 같은 버전)**

Run: `TEST_PY=/Users/junja/Desktop/claude/eink-status-board/.omc/scratch/venv310/bin/python bash /Users/junja/Desktop/claude/eink-status-board/.omc/scratch/tests/run_all.sh 2>&1 | grep -E "^== |^SUMMARY"`
Expected: 8개 스크립트 모두 `0 failed`

- [ ] **Step 3: 브랜치 전체 리뷰** — 새 리뷰어(가장 높은 등급 모델)에게 `git diff main...feat/calendar-suggest` 와 스펙·이 계획을 주고, 특히 인증 흐름(`next`)·락 순서·타이머 수명·XSS(`suggest.html`, Admin 표시)·Python 3.10 호환을 확인받는다. 지적 사항을 검증해 고치고 다시 Step 1–2.

- [ ] **Step 4: push (병합·배포는 사용자 확인 후)**

```bash
cd /Users/junja/Desktop/claude/eink-status-board
git -c credential.helper= -c 'credential.helper=!helper() { echo username=Junja0419; echo "password=$(gh auth token --user Junja0419)"; }; helper' push -u origin feat/calendar-suggest
```

`main` 병합과 VM 배포는 사용자가 브랜치를 확인한 뒤 진행한다. 배포 후 확인: `/healthz` 200, 브라우저로 `/suggest?preset=…` 열기, Power Automate 테스트 일정.
