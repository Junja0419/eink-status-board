# Mac 알림 앱 (Status Board)

일정 제안을 회사 Mac 의 일반 알림으로 받는 메뉴 막대 앱입니다.
일정 시작 5분 전에 화면 오른쪽 위에 알림이 뜨고, 알림의 **옵션**에서 프리셋을 고르면 전자잉크 화면이 바로 바뀝니다.

```
10:00 주간회의
'회의 중'으로 바꿀까요?                              [옵션 ▾]
                                                      '회의 중'으로 바꾸기
                                                      근무 중
                                                      외근 중
                                                      무시
```

- 제안은 Power Automate 가 서버로 보냅니다([Power Automate — Mac 앱으로 받기](power-automate.md#mac-앱으로-받기-http)). 앱은 회사 일정을 직접 읽지 않습니다.
- 고르면 결과가 알림으로 한 번 더 옵니다. 예: `'회의 중'으로 바꿨습니다. 11:00에 '근무 중'으로 돌아갑니다.`
- 알림 본문을 누르면 메뉴 막대 메뉴가 열리고, 같은 선택지가 들어 있습니다. 알림을 놓쳤을 때도 일정이 끝나기 전까지 메뉴에서 고를 수 있습니다.
- 창이나 Dock 아이콘은 없습니다. 메뉴 막대 아이콘이 꽉 찬 사각형이면 연결된 상태, 점선이면 연결이 끊긴 상태입니다.

## 목차

1. [준비 — 서버에 APP_KEY 넣기](#1-준비--서버에-app_key-넣기)
2. [빌드 (개인 Mac)](#2-빌드-개인-mac)
3. [설치 (회사 Mac)](#3-설치-회사-mac)
4. [설정](#4-설정)
5. [문제 해결](#문제-해결)

## 1. 준비 — 서버에 APP_KEY 넣기

앱 전용 키 `APP_KEY` 가 서버 `.env` 에 있어야 합니다. 다른 비밀 값과 달라야 하고 24자 이상이어야 합니다.

```bash
# 서버(VM)에서 — 값은 화면에 찍지 않고 .env 끝에 새 줄로 붙인다 (앞의 \n: 마지막 줄에 줄바꿈이 없어도 이어 붙지 않게)
python3 -c 'import secrets; print("\nAPP_KEY=" + secrets.token_urlsafe(32))' >> ~/eink-status-board/server/.env
sudo systemctl restart eink-status-board
```

이 키는 `/ws/app`(제안 받기)과 `/api/suggestions/apply`(적용)에만 통합니다. Power Automate 에는 `SUGGEST_KEY` 를 넣습니다. 두 키를 서로 바꿔 쓰면 안 됩니다.

## 2. 빌드 (개인 Mac)

Xcode 가 설치된 Mac 에서 만듭니다. Apple Silicon·Intel 공용(universal)이고 macOS 13 이상에서 돌아갑니다.

```bash
bash macos/StatusBoardNotifier/build-app.sh
```

- 결과: `macos/StatusBoardNotifier/build/Status Board.app` 과 옮기기 쉬운 `build/StatusBoard.zip`
- 단위 테스트를 먼저 돌리고 실패하면 멈춥니다.
- 서명은 ad-hoc(개인용)입니다. App Store·공증을 거치지 않으므로 처음 열 때 한 번 허용해야 합니다(아래 3번).

실제 서버에 붙여 보는 통합 테스트도 있습니다. 키는 테스트용 서버의 값을 씁니다.

```bash
cd macos/StatusBoardNotifier
STATUSBOARD_E2E_BASE=http://127.0.0.1:5000 STATUSBOARD_E2E_APP_KEY=<APP_KEY> STATUSBOARD_E2E_SUGGEST_KEY=<SUGGEST_KEY> swift test
```

## 3. 설치 (회사 Mac)

1. `StatusBoard.zip` 을 회사 Mac 으로 옮겨(AirDrop, 메일, 클라우드 드라이브 등) 압축을 풀고, `Status Board.app` 을 **응용 프로그램** 폴더로 옮깁니다.
2. 앱을 엽니다. "확인되지 않은 개발자" 경고가 뜨면:
   - macOS 15 이상: **시스템 설정 → 개인정보 보호 및 보안** 아래쪽의 **그래도 열기**를 누릅니다.
   - macOS 14 이하: 앱을 Control-클릭 → **열기**.
3. "알림을 보내도록 허용하시겠습니까?" 에서 **허용**을 누릅니다.
4. **시스템 설정 → 알림 → Status Board** 에서 알림 스타일을 **알림**(사라지지 않음)으로 둡니다. 배너로 두면 몇 초 뒤 사라져 옵션을 고를 틈이 짧습니다. 메뉴의 **알림 설정 열기…** 로 바로 갈 수 있습니다.

## 4. 설정

처음 실행하면 설정 창이 뜹니다. 나중에는 메뉴 막대 아이콘 → **설정…** 에서 바꿉니다.

| 항목 | 값 |
|------|----|
| 서버 주소 | `https://<도메인>` (Admin 주소에서 `/admin` 을 뺀 것). `http://` 는 이 Mac 안의 `localhost` 만 허용합니다 |
| APP_KEY | 서버 `.env` 의 `APP_KEY` 값. 이 Mac 의 키체인에 저장되며 앱에서 다시 보여 주지 않습니다 |

서버에서 키를 복사하는 방법 — 개인 Mac 터미널에서(화면에 찍히지 않고 클립보드로 갑니다):

```bash
ssh <서버> "sed -n 's/^APP_KEY=//p' ~/eink-status-board/server/.env" | tr -d '\r\n' | pbcopy
```

- **로그인 시 실행**: 메뉴에서 켭니다. 처음 켜면 시스템 설정의 로그인 항목에서 허용을 물을 수 있습니다.
- **지금 다시 연결**: 서버를 재시작했거나 네트워크를 바꾼 뒤 바로 붙이고 싶을 때. 평소에는 앱이 알아서 다시 연결합니다(잠자기에서 깨면 즉시, 끊기면 1·2·5·10·30·60초 간격).

## 문제 해결

| 증상 | 원인과 해결 |
|------|-------------|
| 메뉴에 "접속 거부 — 설정에서 APP_KEY 를 확인하세요" | 키가 서버의 `APP_KEY` 와 다르거나(`SUGGEST_KEY`·`API_KEY` 를 넣었음), 서버에 `APP_KEY` 가 없거나, 앱 연결이 이미 4개 |
| "연결 끊김 — N초 뒤 다시 시도" 가 계속됨 | 서버 주소가 틀렸거나 회사 네트워크가 그 주소를 막음. 같은 Mac 의 브라우저에서 `https://<도메인>/healthz` 가 열리는지 확인 |
| 연결됨인데 알림이 안 옴 | Power Automate 실행 기록에서 HTTP 응답의 `delivered` 를 확인 (0 이면 그 순간 앱이 연결돼 있지 않았음). 시스템 설정 → 알림 → Status Board 가 허용인지, 집중 모드가 막고 있지 않은지 확인 |
| 알림에 옵션이 없음 | 알림 스타일이 **없음**이거나 알림 센터에만 있음 — [설치](#3-설치-회사-mac) 4단계처럼 스타일을 **알림**으로 바꾸기 |
| "손상되었기 때문에 열 수 없습니다" | 옮기는 중 붙은 격리 표시 때문 — 터미널에서 `xattr -dr com.apple.quarantine "/Applications/Status Board.app"` 후 다시 열기 |
| 열 때마다 "키체인 접근" 을 물음 | 앱을 새로 빌드해 바꾸면 macOS 가 같은 앱인지 다시 묻습니다. **항상 허용**을 누르면 그 버전에서는 다시 묻지 않습니다 |
| 옵션을 골랐는데 "바꾸지 못했습니다" | 결과 알림의 문장을 확인 — "이미 끝난 일정입니다"(일정 종료 뒤 고름), "프리셋을 찾을 수 없습니다"(그사이 Admin 에서 지움), 키 오류 |
| 앱 로그를 보고 싶을 때 | `log stream --info --predicate 'subsystem == "io.github.junja0419.statusboard-notifier"'` — 연결 상태, 제안 id, 적용 결과가 찍힙니다(일정 제목은 찍지 않음) |
