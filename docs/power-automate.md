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
