# Power Automate 로 일정 제안 받기

회사 Outlook 일정이 시작하기 5분 전에 **"'회의 중'으로 바꾸시겠어요?"** 제안을 받고, 한 번 골라 전자잉크 화면을 바꿉니다.
일정이 끝나면 서버가 바꾸기 전 상태로 되돌립니다(그 사이 직접 다른 상태로 바꿨다면 건드리지 않습니다).
제안을 받는 곳은 둘 중 하나입니다.

- **Mac 알림 앱** — 회사 Mac 오른쪽 위 알림에서 바로 고릅니다. [Mac 앱으로 받기](#mac-앱으로-받기-http) (HTTP 동작 필요)
- **Teams 링크 메시지** — 링크를 열어 **[바꾸기]** 를 누릅니다. 아래 흐름 A 그대로 (Standard 커넥터만)

```
Outlook 일정 ─(시작 5분 전)→ Power Automate ─┬→ 서버 ─→ Mac 앱 알림 ─[옵션에서 선택]→ 화면 변경
                                             └→ Teams 메시지 ─(링크)→ /suggest ─[바꾸기]→ 화면 변경
                                                         일정 종료 시각 → 이전 상태로 복귀
```

- 서버는 일정을 읽지 않습니다. 회사 안(Power Automate)에서 일정 제목으로 프리셋을 고릅니다.
  - 링크에는 **프리셋 이름과 종료 시각만** 들어갑니다.
  - Mac 앱으로 받을 때는 알림에 보일 일정 제목이 서버를 거쳐 갑니다. 서버는 메모리에만 두고 로그·파일에 남기지 않습니다.
- 링크 메시지 방식은 Office 365 Outlook·Microsoft Teams 두 커넥터만 쓰며 모두 **Standard** 입니다. Mac 앱으로 받으려면 Premium **HTTP** 동작이 추가로 필요합니다.
- 링크를 열어도 아무것도 바뀌지 않습니다. [바꾸기] 를 눌러야 바뀌고, Google 로그인이 필요합니다.

## 목차

1. [흐름 A — 일정 시작 전 제안](#흐름-a--일정-시작-전-제안)
   - [Mac 앱으로 받기 (HTTP)](#mac-앱으로-받기-http)
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
| 2 | **Filter array** (데이터 작업, 한국어 화면: 배열 필터링) | From: 아래 ⓪ 을 **fx(식)** 로 · 고급 모드 식: 아래 ① |
| 3 | **Apply to each** (컨트롤, 한국어 화면: 각각에 적용) | 이전 단계에서 출력 선택: 동적 콘텐츠에서 2번 동작의 **본문(Body)** |
| 4 | (3 안) **Compose**(데이터 작업, 한국어 화면: 작성) — 이름을 `Preset` 으로 (`…` → 이름 바꾸기) | 입력: 아래 ② 를 그대로 붙여넣기 |
| 5 | (3 안) **Compose** — 이름을 `Link` 로 | 입력: 아래 ③ 을 그대로 붙여넣기 |
| 6 | (3 안) **Post message in a chat or channel** (Microsoft Teams) | Post as: `Flow bot` · Post in: `Chat with Flow bot` · Recipient: 내 회사 이메일 · Message: 아래 ④ |

> **식 넣는 법**: ⓪ 은 입력칸의 **fx** 탭에 `@{ }` 없이 넣습니다 — `@{식}` 은 결과를 **문자열**로 바꾸므로 배열이 필요한
> From 에 쓰면 `The 'from' property value … is of type 'String'` 으로 실패합니다.
> ②·③·④ 는 결과가 글자라 `@{ }` 로 감싼 그대로 붙여넣으면 됩니다(감싸지 않고 글 칸에 넣으면 식이 글자 그대로 나갑니다).
> 한국어 화면에서는 동작 기본 이름이 영어와 달라 `body('Filter_array')` 같은 이름 참조가 틀릴 수 있으니, 이전 단계 값은 동적 콘텐츠에서 고릅니다.
> ③ 의 `outputs('Preset')` 은 4번 동작 이름이 정확히 `Preset` 일 때만 동작합니다.

⓪ From — 트리거의 **Split On** 설정(일정마다 따로 실행 / 목록으로 한 번에)과 상관없이 배열로 맞춥니다:

```
if(equals(triggerBody()?['value'], null), createArray(triggerBody()), triggerBody()?['value'])
```

① 제외 조건 — 종일 일정, "약속 없음(free)", 거절한 일정, 취소된 일정:

```
@and(equals(item()?['isAllDay'], false), not(equals(item()?['showAs'], 'free')), not(equals(item()?['responseType'], 'declined')), not(startsWith(coalesce(item()?['subject'], ''), 'Canceled:')), not(startsWith(coalesce(item()?['subject'], ''), '취소됨:')))
```

② 제목 키워드 → 프리셋 이름 (Admin 에 있는 이름과 **글자까지 같아야** 합니다):

```
@{if(contains(toLower(coalesce(item()?['subject'], '')), '점심'), '점심 식사', if(contains(toLower(coalesce(item()?['subject'], '')), '외근'), '외근 중', '회의 중'))}
```

③ 링크 — `<도메인>` 을 내 서버 주소로 바꿉니다. 종료 시각은 반드시 **`endWithTimeZone`** (오프셋이 붙은 값)을 씁니다:

```
@{concat('https://<도메인>/suggest?preset=', encodeUriComponent(outputs('Preset')), '&until=', encodeUriComponent(item()?['endWithTimeZone']))}
```

④ Teams 메시지 — 메시지 칸 도구 모음의 **`</>`(코드 보기)** 를 켜고 붙여넣습니다. 일반 보기에 넣으면 `<p>` 같은 태그가 글자 그대로 보입니다:

```html
<p><b>@{convertTimeZone(item()?['start'], 'UTC', 'Korea Standard Time', 'HH:mm')} @{item()?['subject']}</b></p>
<p>'@{outputs('Preset')}'(으)로 바꾸시겠어요?</p>
<p><a href="@{outputs('Link')}">바꾸기 화면 열기</a></p>
```

### Mac 앱으로 받기 (HTTP)

흐름 A 의 6번(메시지 게시) 대신 아래 6번을 쓰면, 회사 Mac 의 [Status Board 앱](macos-app.md)이 화면 오른쪽 위에 알림을 띄웁니다.
알림의 **옵션**에서 제안된 프리셋이나 다른 프리셋을 고르면 바로 바뀌고, 일정이 끝나면 서버가 원래 상태로 되돌립니다.
Power Automate 의 **HTTP** 동작(Premium)이 필요합니다. 서버 `.env` 에는 `SUGGEST_KEY`(흐름용)와 `APP_KEY`(앱용)가 있어야 합니다.

| # | 동작 | 설정 |
|---|------|------|
| 6 | (3 안) **HTTP** | 방법 `POST` · URI `https://<도메인>/api/suggestions/notify` · 헤더 `X-API-Key` = `SUGGEST_KEY` 값, `Content-Type` = `application/json` · 본문: 아래 ⑤ 를 **fx(식)** 로 · 동작 **설정**에서 **보안 입력**·**보안 출력** 켜기, **재시도 정책** `없음` |
| 7 | (선택, 3 안) **Condition** — 앱이 꺼져 있을 때 Teams 로도 받기 | 왼쪽 fx `body('HTTP')?['delivered']`(6번 동작 이름이 `HTTP` 가 아니면 그 이름) · `같음` · 오른쪽 fx `0` · **예** 분기에 흐름 A 의 6번(④ 링크 메시지 게시)을 넣음 · 이 Condition 의 **실행 후 설정**에서 6번이 *실패함*·*시간 초과됨*일 때도 실행되게 체크(서버가 응답하지 않아도 Teams 로 받게) |

⑤ 본문 — 제목에 `"` 가 있어도 깨지지 않도록 글자가 아니라 객체로 만듭니다. Body 칸의 **fx** 탭에 `@{ }` 없이 넣습니다:

```
addProperty(addProperty(addProperty(addProperty(json('{}'), 'name', outputs('Preset')), 'subject', coalesce(item()?['subject'], '')), 'start', item()?['startWithTimeZone']), 'until', item()?['endWithTimeZone'])
```

- 서버는 화면을 바꾸지 않고 앱에 제안만 보낸 뒤 `{"id": "…", "delivered": 1}` 을 돌려줍니다. `delivered` 는 서버가 제안을 써 보낸 앱 연결 수입니다. Mac 이 막 잠든 뒤 1분 정도는 끊긴 연결도 1로 셀 수 있습니다. 그래도 제안은 서버에 남아 있어 Mac 이 깨면 다시 갑니다.
- 앱이 꺼져 있거나 Mac 이 잠자고 있으면, 일정이 끝나기 전까지 서버가 제안을 들고 있다가 앱이 다시 연결될 때 보냅니다. 서버를 재시작하면 들고 있던 제안은 사라집니다.
- 실패는 실행 기록에 남습니다: 없는 프리셋 이름 `404`, 이미 끝난 일정 `409`, 본문 형식 오류 `422`, 키 오류 `401`.
- 재시도 정책을 `없음`으로 두는 이유: 응답만 잃은 재시도가 같은 일정의 알림을 하나 더 띄우지 않게 하려는 것입니다.
- 보안 입력은 실행 기록에서만 키를 가립니다. 흐름 공동 소유자와 테넌트 관리자는 흐름 정의에서 키를 볼 수 있습니다. 걱정되면 환경 변수(비밀)나 Azure Key Vault 로 옮기세요.
- 회사 데이터 정책(DLP)이 HTTP 커넥터를 막아 두었으면 저장할 때 오류가 납니다. 그때는 흐름 A 의 6번(Teams 링크 메시지)을 그대로 씁니다.
- `SUGGEST_KEY` 는 흐름에만, `APP_KEY` 는 Mac 앱에만 넣습니다. 서로 바꿔 넣으면 `401`(앱은 "접속 거부") 입니다.
- Teams 적응형 카드에서 바로 적용하는 방식(`POST /api/suggestions/accept`)도 서버에 남아 있습니다([API 레퍼런스](../README.md#일정-제안)).

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

Mac 앱으로 받을 때는 2·3번이 이렇게 바뀝니다.

2. 2분쯤 뒤 회사 Mac 오른쪽 위에 알림이 오는지 봅니다. 실행 기록에서 HTTP 동작의 출력 `delivered` 가 1 이상인지 확인합니다.
3. 알림의 **옵션** → 제안된 프리셋을 고르면 패널이 바뀌고, "…으로 바꿨습니다. HH:MM에 '…'(으)로 돌아갑니다." 결과 알림이 옵니다.

## 문제 해결

| 증상 | 원인과 해결 |
|------|-------------|
| 메시지가 안 옴 | 실행 기록이 없으면 트리거가 안 돈 것 — Calendar Id 가 기본 일정인지 확인. Microsoft 는 드물게 트리거가 최대 1시간 늦을 수 있다고 안내합니다 |
| 배열 필터링 실패 `The 'from' property value … is of type 'String'` | From 에 `@{…}` 로 넣어 문자열이 됐음 — ⓪ 을 fx 탭에 `@{}` 없이 다시 넣으세요 |
| 메시지 게시 실패 `convertFromUtc … expects … a UTC date time` | 트리거의 `start`·`end` 는 끝에 `Z` 가 없어 `convertFromUtc` 가 거부함 — ④ 처럼 `convertTimeZone(…, 'UTC', 'Korea Standard Time', 'HH:mm')` 을 쓰세요 |
| 메시지에 `if(contains(…` 나 `concat('https://…` 가 글자 그대로 보임 | Compose 입력에 `@{ }` 없이 식만 넣었음 — ②·③ 을 `@{ }` 로 감싼 그대로 다시 붙여넣으세요 |
| 메시지에 `<p>`·`<b>` 태그가 보임 | 메시지를 일반 보기에 붙여넣었음 — `</>`(코드 보기)에서 붙여넣거나, 태그 없이 세 줄로 쓰세요(주소는 Teams 가 자동으로 링크로 만듦) |
| HTTP(notify) 가 `401` | 헤더 이름이 `X-API-Key` 가 아니거나, 값이 서버 `.env` 의 `SUGGEST_KEY` 와 다르거나, 서버에 `SUGGEST_KEY` 가 없음. 단축어용 `API_KEY`·앱용 `APP_KEY` 는 이 경로에 통하지 않음 |
| HTTP(notify) 가 `404` | ② 의 이름과 Admin 의 프리셋 이름이 다름 (띄어쓰기 포함) |
| HTTP(notify) 가 `422` | 본문이 객체가 아님(⑤ 를 글자 칸에 붙여 넣었음 — fx 탭에 넣으세요) 또는 `start`·`until` 이 시각이 아님 |
| HTTP 는 `200` 인데 Mac 에 알림이 없음 | 응답의 `delivered` 가 0 이면 앱이 연결돼 있지 않음 — [Mac 알림 앱 문제 해결](macos-app.md#문제-해결) |
| 6번 동작 실패 `BotNotInConversationRoster` 등 | Flow bot(Workflows 앱) 차단 — [메일로 받기](#teams-가-막혀-있을-때--메일로-받기) |
| 링크를 열면 로그인 화면 | Teams 가 링크를 앱 안의 브라우저로 열면 Chrome 의 로그인 상태를 쓰지 못합니다. 한 번 로그인하면 원래 제안 화면으로 돌아오고, 그 브라우저에는 30일간 유지됩니다. 로그인이 막히면 "브라우저에서 열기" 로 Chrome 에서 여세요 |
| "프리셋이 없습니다" | ② 의 이름과 Admin 의 프리셋 이름이 다름 (띄어쓰기 포함) |
| 복귀 시각이 9시간 어긋남 | 링크의 `until` 에 한국 시간을 오프셋 없이 넣었음 — 서버는 오프셋이 없으면 UTC 로 읽습니다. 흐름 A 는 ③ 처럼 `endWithTimeZone`(트리거의 `end` 도 UTC 라 괜찮음), 흐름 B 는 `convertToUtc(...)` 결과를 쓰세요 |
| "이미 끝난 일정입니다" | 일정 종료 뒤에 링크를 열었음 — 의도된 동작 |
| 일정 종료 후 돌아오지 않음 | 그 사이 직접 다른 상태로 바꿨거나, Admin 에서 [취소] 했거나, 되돌릴 프리셋을 지웠음 |
