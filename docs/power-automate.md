# Power Automate 로 일정 제안 받기

회사 Outlook 일정이 시작하기 5분 전에 Teams 로 **"'회의 중'으로 바꾸시겠어요?"** 메시지를 받고,
링크를 열어 **[바꾸기]** 한 번이면 전자잉크 화면이 바뀝니다. 일정이 끝나면 서버가 바꾸기 전 상태로 되돌립니다
(그 사이 직접 다른 상태로 바꿨다면 건드리지 않습니다).

```
Outlook 일정 ─(시작 5분 전)→ Power Automate ─→ Teams 메시지 ─(링크)→ https://<도메인>/suggest ─[바꾸기]→ 화면 변경
                                                                                     └ 일정 종료 시각 → 이전 상태로 복귀
```

- 서버는 일정을 읽지 않습니다. 회사 안(Power Automate)에서 일정 제목으로 프리셋을 고르고, 링크에는 **프리셋 이름과 종료 시각만** 들어갑니다. 일정 제목은 Teams 메시지 안에만 남습니다.
- 링크 메시지 방식은 Office 365 Outlook·Microsoft Teams 두 커넥터만 쓰며 모두 **Standard** 입니다. [버튼 카드로 바로 적용](#버튼-카드로-바로-적용-http)하려면 Premium **HTTP** 동작이 추가로 필요합니다.
- 링크를 열어도 아무것도 바뀌지 않습니다. [바꾸기] 를 눌러야 바뀌고, Google 로그인이 필요합니다.

## 목차

1. [흐름 A — 일정 시작 전 제안](#흐름-a--일정-시작-전-제안)
   - [버튼 카드로 바로 적용 (HTTP)](#버튼-카드로-바로-적용-http)
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

### 버튼 카드로 바로 적용 (HTTP)

흐름 A 의 6번(메시지 게시) 대신 아래 6~9번을 쓰면 Teams 카드의 **[바꾸기]** 한 번으로 바로 적용됩니다(브라우저가 열리지 않음).
Power Automate 의 **HTTP** 동작(Premium)이 필요하고, 서버 `.env` 에 `SUGGEST_KEY` 가 있어야 합니다.

| # | 동작 | 설정 |
|---|------|------|
| 6 | (3 안) **Post adaptive card and wait for a response** (Microsoft Teams, 한국어 화면: 적응형 카드를 게시하고 응답 대기) | Post as `Flow bot` · Post in `Chat with Flow bot` · Recipient 내 회사 이메일 · Message: 아래 ⑤ · Update message: `응답을 받았습니다` · 동작 **설정**의 **시간 제한**: `PT2H` |
| 7 | (3 안) **Condition** (컨트롤, 한국어 화면: 조건) | 왼쪽: 동적 콘텐츠에서 6번의 **submitActionId** · `같음` · 오른쪽: `apply` |
| 8 | (예인 경우) **HTTP** | 방법 `POST` · URI `https://<도메인>/api/suggestions/accept` · 헤더 `X-API-Key` = `SUGGEST_KEY` 값, `Content-Type` = `application/json` · 본문: 아래 ⑥ · 동작 **설정**에서 **보안 입력**·**보안 출력** 켜기(실행 기록에 키가 남지 않게), **재시도 정책** `없음`(응답만 잃은 재시도가 "이미 … 상태입니다" 로 답하지 않게) |
| 9 | (예인 경우) **Post message in a chat or channel** | 6번과 같은 대상 · Message: 동적 콘텐츠에서 8번의 **본문(Body)** |

⑤ 카드 — 그대로 붙여넣습니다. 제목의 `"`·`\` 는 카드 형식을 깨므로 `"` 는 지우고 `\` 는 `/` 로 바꿉니다(식 안에서 `decodeUriComponent` 로 표기):

```json
{
  "type": "AdaptiveCard",
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "version": "1.4",
  "body": [
    {
      "type": "TextBlock", "weight": "Bolder", "wrap": true,
      "text": "@{convertTimeZone(item()?['start'], 'UTC', 'Korea Standard Time', 'HH:mm')} @{replace(replace(coalesce(item()?['subject'], ''), decodeUriComponent('%5C'), '/'), decodeUriComponent('%22'), '')}"
    },
    { "type": "TextBlock", "wrap": true, "text": "'@{outputs('Preset')}'(으)로 바꾸시겠어요?" }
  ],
  "actions": [
    { "type": "Action.Submit", "id": "apply", "title": "바꾸기" },
    { "type": "Action.Submit", "id": "skip", "title": "무시" },
    { "type": "Action.OpenUrl", "title": "다른 프리셋", "url": "@{outputs('Link')}" }
  ]
}
```

⑥ HTTP 본문:

```json
{ "name": "@{outputs('Preset')}", "until": "@{item()?['endWithTimeZone']}" }
```

- 서버는 처리한 경우 모두 `200` 과 한 줄 문장을 돌려줍니다 — 예: `'회의 중'으로 바꿨습니다. 15:00에 '근무 중'으로 돌아갑니다.`, `'회의 중' 상태를 유지합니다. …`(연속 일정), `이미 '회의 중' 상태입니다.`, `이미 끝난 일정이라 바꾸지 않았습니다.`, `'…' 프리셋이 없습니다. …`, `'…' 프리셋을 불러오지 못해 바꾸지 않았습니다. …`(이미지 파일 없음). 9번은 그 문장을 그대로 보냅니다. 키가 틀리면 `401` 로 8번이 실패하고 9번은 실행되지 않습니다(실행 기록에서 확인).
- [무시] 를 누르면 7번이 아니오로 끝나고 아무것도 바뀌지 않습니다. 2시간 안에 응답이 없으면 그 실행은 시간 초과로 끝나며(실행 기록에 실패로 보임) 정상입니다.
- `SUGGEST_KEY` 는 이 경로에만 통합니다. 단축어용 `API_KEY` 를 넣으면 `401` 입니다. 키를 바꾸려면 서버 `.env` 를 고치고 재시작한 뒤 8번 헤더도 바꿉니다.
- 보안 입력은 실행 기록에서만 키를 가립니다. 흐름 공동 소유자와 테넌트 관리자는 흐름 정의에서 키를 볼 수 있습니다. 걱정되면 환경 변수(비밀)나 Azure Key Vault 로 옮기세요.
- 회사 데이터 정책(DLP)이 HTTP 커넥터를 막아 두었으면 저장할 때 오류가 납니다. 그때는 링크 메시지 방식(6번)을 그대로 씁니다.

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
| 배열 필터링 실패 `The 'from' property value … is of type 'String'` | From 에 `@{…}` 로 넣어 문자열이 됐음 — ⓪ 을 fx 탭에 `@{}` 없이 다시 넣으세요 |
| 메시지 게시 실패 `convertFromUtc … expects … a UTC date time` | 트리거의 `start`·`end` 는 끝에 `Z` 가 없어 `convertFromUtc` 가 거부함 — ④ 처럼 `convertTimeZone(…, 'UTC', 'Korea Standard Time', 'HH:mm')` 을 쓰세요 |
| 메시지에 `if(contains(…` 나 `concat('https://…` 가 글자 그대로 보임 | Compose 입력에 `@{ }` 없이 식만 넣었음 — ②·③ 을 `@{ }` 로 감싼 그대로 다시 붙여넣으세요 |
| 메시지에 `<p>`·`<b>` 태그가 보임 | 메시지를 일반 보기에 붙여넣었음 — `</>`(코드 보기)에서 붙여넣거나, 태그 없이 세 줄로 쓰세요(주소는 Teams 가 자동으로 링크로 만듦) |
| 8번 HTTP 가 `401` | 헤더 이름이 `X-API-Key` 가 아니거나, 값이 서버 `.env` 의 `SUGGEST_KEY` 와 다르거나(단축어용 `API_KEY` 가 아님), 서버에 `SUGGEST_KEY` 가 없음 |
| 8번 HTTP 가 `422` | 본문 JSON 형식이 틀림 — ⑥ 을 그대로 쓰고, `name` 이 비어 있지 않은지 확인 |
| 6번 동작 실패 `BotNotInConversationRoster` 등 | Flow bot(Workflows 앱) 차단 — [메일로 받기](#teams-가-막혀-있을-때--메일로-받기) |
| 링크를 열면 로그인 화면 | Teams 가 링크를 앱 안의 브라우저로 열면 Chrome 의 로그인 상태를 쓰지 못합니다. 한 번 로그인하면 원래 제안 화면으로 돌아오고, 그 브라우저에는 30일간 유지됩니다. 로그인이 막히면 "브라우저에서 열기" 로 Chrome 에서 여세요 |
| "프리셋이 없습니다" | ② 의 이름과 Admin 의 프리셋 이름이 다름 (띄어쓰기 포함) |
| 복귀 시각이 9시간 어긋남 | 링크의 `until` 에 한국 시간을 오프셋 없이 넣었음 — 서버는 오프셋이 없으면 UTC 로 읽습니다. 흐름 A 는 ③ 처럼 `endWithTimeZone`(트리거의 `end` 도 UTC 라 괜찮음), 흐름 B 는 `convertToUtc(...)` 결과를 쓰세요 |
| "이미 끝난 일정입니다" | 일정 종료 뒤에 링크를 열었음 — 의도된 동작 |
| 일정 종료 후 돌아오지 않음 | 그 사이 직접 다른 상태로 바꿨거나, Admin 에서 [취소] 했거나, 되돌릴 프리셋을 지웠음 |
