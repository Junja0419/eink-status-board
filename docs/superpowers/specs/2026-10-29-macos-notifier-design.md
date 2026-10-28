# macOS 알림 앱 (일정 제안) — 설계

2026-10-29. 사용자 승인: 채팅에서 흐름·앱 모양 정리를 보고 "제안해준 대로 만들어줄래" (앱 설치 가능 확인).

## 왜

일정 제안은 지금 Teams 메시지(링크 또는 적응형 카드)로 온다. 사용자는 회사 Mac에서 **일반 macOS 알림**을 받고, 그 알림 안에서 제안된 프리셋으로 바로 바꾸거나 드롭다운에서 다른 프리셋을 고르고 싶다.

## 확정된 것 (사용자)

- 알림은 **회사 Mac**에 뜬다. 회사 일정은 그 Mac의 Outlook 앱에만 있고, 기본 캘린더 앱에 회사 계정을 추가할 수 없다. 그래서 앱이 일정을 직접 읽지 않는다.
- 일정 감지는 지금의 Power Automate 흐름 A를 그대로 쓴다. Premium HTTP 동작은 쓸 수 있다.
- 흐름: Power Automate → `POST /api/suggestions/notify` → 서버 → 회사 Mac 앱(WebSocket) → macOS 알림 → [바꾸기] 또는 다른 프리셋 → 서버 적용 → 일정 종료 시 기존 `pending_revert`로 복귀.
- 앱: 창 없는 메뉴 막대 앱, 로그인 시 자동 실행, 메뉴에 현재 연결 상태.
- Teams 카드는 쓰지 않는다. 앱 설치는 문제없다.

## 목표 / 비목표 / 실패 조건

- 목표: 5분 전 알림, 클릭 한 번으로 적용, 다른 프리셋 선택, 끝나면 자동 복귀, 결과 알림.
- 비목표: 앱에서 일정 직접 읽기, 프리셋 관리(Admin이 한다), 수시 수동 전환 UI, App Store·공증 배포, iOS.
- 실패 조건: 일정 제목이 서버에 저장되거나 로그에 남는 것. 키 하나로 다른 용도의 경로가 열리는 것. 알림을 놓치면 제안이 사라져 다시 볼 수 없는 것.

## 서버 (내가 정한 것)

- **새 비밀 `APP_KEY`** (24자 이상, 다른 비밀과 달라야 함). 용도별 전용 키 원칙에 따른다.
  - `/ws/app` 핸드셰이크: `X-API-Key` 또는 `Authorization: Bearer`. 틀리면 accept 전에 close(1008).
  - `APP_KEY_PATHS = {"/api/suggestions/apply"}`: 앱의 적용 요청. 기존 라우트(ID + until)를 그대로 쓴다.
  - 비우면 앱은 접속할 수 없다.
- **`POST /api/suggestions/notify`** (`SUGGEST_KEY_PATHS`에 추가: 같은 Power Automate 용도).
  - 본문 `{name, subject?, start?, until?}`. `extra="forbid"`, 이름 100자·시각 64자·제목 1,000자 상한(알림에는 120자로 자른다).
  - 이름으로 프리셋을 찾고 없으면 404, until이 지났으면 409. Power Automate 실행 기록에 실패로 남도록 정상 상태 코드를 쓴다.
  - 응답 `{"id", "delivered": <받은 앱 수>}`.
- **제안 메시지**(서버가 문장을 만든다. 조사·KST 시각 규칙을 한곳에 둔다): `{"type":"suggestion", "id", "title", "body", "preset_id", "preset_name", "until", "expires_at", "presets":[{id,name}]}`.
  - title 예: `10:00 주간회의`. body 예: `'회의 중'으로 바꿀까요?`
  - 제목의 제어 문자는 지우고 공백은 합친다. 제목은 메모리에만 두고 로그·state.json에는 남기지 않는다.
- **보관**: 만료 전의 제안을 메모리에 최대 20개 둔다. 만료 시각은 until, until이 없으면 1시간 뒤다. 앱이 (재)접속하면 `hello` 다음에 살아 있는 제안을 모두 다시 보낸다. 중복 표시는 앱이 id로 거른다. 재시작하면 사라진다(영속화 안 함).
- **결과 문장**: accept의 문장 로직을 `_suggestion_summary`로 빼서, apply 응답에 `"message"`로 추가한다(기존 필드 유지).
- 동시 앱 연결 4개. 앱이 보내는 메시지는 무시하고(크기 제한), ping은 uvicorn 기본값을 쓴다.

## 앱 (내가 정한 것)

- Swift + AppKit, SwiftPM 패키지 `macos/StatusBoardNotifier`, macOS 13+, universal(arm64 + x86_64), ad-hoc 서명.
  - `build-app.sh`가 `.app`과 zip을 만든다. `LSUIElement`(Dock 아이콘 없음), `NSUserNotificationAlertStyle=alert`(알림이 사라지지 않음).
- 설정: 서버 주소(UserDefaults)와 `APP_KEY`(키체인). 메뉴의 "설정…" 대화상자에서 넣는다.
- 연결: `URLSessionWebSocketTask`. 백오프 1·2·5·10·30·60초로 재연결하고, 잠자기에서 깨면 즉시 재연결한다. 25초마다 ping.
- 알림: 제안마다 카테고리를 등록한다. 동작 순서는 제안 프리셋("'회의 중'으로 바꾸기") → 나머지 프리셋(최대 8개 — 알림 동작이 10개를 넘지 않게) → "무시".
  - macOS는 동작이 여럿이면 "옵션" 드롭다운으로 묶는다. 알림 본문을 누르면 메뉴 막대 메뉴가 열리고, 같은 선택지가 들어 있다.
  - 적용 결과(서버 `message`)나 오류는 새 알림으로 보여 준다. 만료된 제안 알림은 지운다.
- 메뉴: 연결 상태, 최근 제안과 그 선택지, 설정…, 로그인 시 실행(SMAppService), 종료.

## Power Automate

흐름 A의 6번을 HTTP `POST /api/suggestions/notify` 하나로 바꾼다. 헤더는 `SUGGEST_KEY`, 본문은 `{name, subject, start, until}`이다.
- 앱이 꺼져 있을 때를 위한 예비 Teams 링크 메시지는 선택 사항이다. `delivered`가 0이면 보내는 조건을 문서에 적는다.

## 검증

- 서버 하네스 `t_app.py`: 키 격리, WS 인증, 전달·재전달·만료, 404/409/422, 문장, 연결 상한. 3.10과 3.14에서 돌린다.
- 앱: 순수 로직은 `NotifierCore`에서 XCTest로 검증한다. 이 Mac에서 로컬 서버에 붙여 알림 표시와 적용까지 직접 확인한다.
