# GCP 무료 티어 배포 가이드

GCP(Google Cloud Platform) e2-micro **Always Free** 인스턴스에 서버를 올리고,
DuckDNS 도메인 + Caddy(HTTPS) + Google 로그인으로 보호한 채 systemd 로 24시간 구동하는 절차입니다.

HTTPS 와 인증은 선택 사항이 아니라 **전제**입니다. 서버는 인증 설정이 없으면 시작하지 않습니다(fail closed).
이 가이드를 끝까지 따르면 다음 구성이 됩니다.

| 대상 | 접속 방식 | 자격 증명 |
|------|-----------|-----------|
| 브라우저 (Admin) | `https://<도메인>.duckdns.org/admin` | Google 로그인 + `ALLOWED_EMAILS` |
| iPhone 단축어 / curl | `https://<도메인>.duckdns.org/api/shortcuts/*` | `X-API-Key` 헤더 |
| ESP32 | `wss://<도메인>.duckdns.org/ws` (443) | `X-Device-Token` 헤더 |

Caddy 가 80/443 에서 TLS 를 끝내고 `127.0.0.1:5000` 의 서버로 넘깁니다. 5000 포트는 외부에 열지 않습니다.

## 목차

1. [인프라 요구사항](#1-인프라-요구사항)
2. [초기 시스템 설정](#2-초기-시스템-설정)
3. [Python 환경과 소스 (uv)](#3-python-환경과-소스-uv)
4. [한글 폰트](#4-한글-폰트)
5. [DuckDNS 자동 갱신](#5-duckdns-자동-갱신)
6. [Caddy (HTTPS)](#6-caddy-https)
7. [Google 로그인 설정](#7-google-로그인-설정)
8. [서버 환경변수 (.env)](#8-서버-환경변수-env)
9. [systemd 서비스 등록](#9-systemd-서비스-등록)
10. [방화벽 정리](#10-방화벽-정리)
11. [ESP32 펌웨어 연결](#11-esp32-펌웨어-연결)
12. [동작 검증 체크리스트](#12-동작-검증-체크리스트)
13. [업데이트](#13-업데이트)
14. [백업과 복구](#14-백업과-복구)
15. [보안 모델](#15-보안-모델)
16. [운영 팁](#16-운영-팁)
17. [선택 사항](#17-선택-사항)

---

## 1. 인프라 요구사항

| 항목 | 값 | 비고 |
|------|----|------|
| Region | `us-central1` / `us-west1` / `us-east1` | Always Free 대상 리전 |
| Machine type | `e2-micro` | |
| Boot disk | **Standard Persistent Disk**, 30 GB 이하 | 🚨 SSD를 고르면 과금됩니다 |
| OS | Ubuntu 22.04 LTS 이상 | Debian도 동일 절차 |
| Firewall | TCP **80, 443** 인바운드 허용 | VM 생성 시 "HTTP/HTTPS 트래픽 허용" 체크 (네트워크 태그 `http-server`, `https-server`). **5000 은 열지 않습니다** |
| 도메인 | [DuckDNS](https://www.duckdns.org) 서브도메인 + 토큰 | |
| Google 계정 | OAuth 클라이언트 생성용 | [google-oauth.md](google-oauth.md) |

이미 만든 VM 이라면 로컬 PC 나 Cloud Shell 에서 태그를 붙입니다. 기본 규칙 `default-allow-http` / `default-allow-https` 가 이 태그를 가진 인스턴스에 80/443 을 엽니다.

```bash
gcloud compute instances add-tags <인스턴스> --zone <존> --tags http-server,https-server
gcloud compute firewall-rules list        # default-allow-http / default-allow-https 가 있는지 확인
```

## 2. 초기 시스템 설정

```bash
sudo apt update && sudo apt install -y cron git curl
sudo systemctl enable --now cron
```

## 3. Python 환경과 소스 (uv)

```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"   # 또는 새 셸 열기

# 소스 클론 — 아래 경로를 9절 systemd 유닛과 동일하게 유지하세요
git clone https://github.com/<본인_계정>/eink-status-board.git ~/eink-status-board
cd ~/eink-status-board

# 가상환경(저장소 루트의 .venv) + 의존성
uv venv
uv pip install --python .venv/bin/python -r server/requirements.txt
```

Python 3.10 이상이면 됩니다. 이 단계에서 `python main.py` 를 바로 실행하면 `인증 설정 누락` 오류로 종료되는 것이 정상입니다 (8절에서 `.env` 를 만든 뒤 실행).

## 4. 한글 폰트

텍스트 프리셋은 서버가 글자를 직접 그리므로 한글 폰트가 필요합니다. 없으면 텍스트 프리셋 생성이 `503` 으로 실패합니다.

```bash
sudo apt install -y fonts-nanum
```

서버가 `/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf` 를 자동으로 찾습니다. 다른 폰트를 쓰려면 `.env` 에 `FONT_PATH=/경로/폰트.ttf` 를 지정하세요.

## 5. DuckDNS 자동 갱신

인스턴스 재시작으로 외부 IP가 바뀌어도 도메인이 따라오도록 5분마다 갱신합니다.
DuckDNS 사이트에서 서브도메인을 만들고, 페이지 상단의 **token** 을 확인해 두세요. 도메인 칸에는 `.duckdns.org` 를 뺀 이름만 넣습니다.

```bash
mkdir -p ~/duckdns && cd ~/duckdns
install -m 600 /dev/null token.env                                       # 600 권한 빈 파일
printf 'DUCKDNS_DOMAIN=<도메인>\nDUCKDNS_TOKEN=<토큰>\n' > token.env
cat > duck.sh <<'SH'
#!/bin/sh
# 토큰은 token.env(600)에서 읽고, URL 을 stdin 으로 넘겨 프로세스 목록에 보이지 않게 한다
. "$HOME/duckdns/token.env"
printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip="\n' "$DUCKDNS_DOMAIN" "$DUCKDNS_TOKEN" \
  | curl -fsS -o "$HOME/duckdns/duck.log" -K -
SH
chmod 700 duck.sh && ./duck.sh && cat duck.log   # OK 가 나와야 함

# 5분 주기 + 부팅 직후 1회
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "@reboot ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

도메인이 VM 의 외부 IP 를 가리키는지 확인합니다. (Caddy 인증서 발급 전에 반드시 맞아야 합니다)

```bash
getent hosts <도메인>.duckdns.org
```

## 6. Caddy (HTTPS)

Caddy 는 DuckDNS 도메인에 대해 Let's Encrypt 인증서를 자동으로 발급·갱신하고, HTTP 80 을 HTTPS 로 리다이렉트하며, WebSocket 도 별도 설정 없이 프록시합니다.

```bash
# 공식 apt 저장소로 설치
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile` 를 아래 내용으로 교체합니다 (`<도메인>` 수정, 들여쓰기는 탭).

```bash
sudo nano /etc/caddy/Caddyfile
```

```
<도메인>.duckdns.org {
	encode gzip

	request_body {
		max_size 6MB
	}

	header {
		Strict-Transport-Security "max-age=15552000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		-Server
	}

	reverse_proxy 127.0.0.1:5000
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30        # 인증서 발급 성공 메시지 확인
```

| 설정 | 이유 |
|------|------|
| `request_body max_size 6MB` | 업로드 한도 5MB + multipart 오버헤드. 서버도 5MB 를 넘으면 413 을 돌려줍니다 |
| 접근 로그를 켜지 않음 (`log` 지시어 없음) | `X-API-Key` / `X-Device-Token` 헤더가 로그에 남지 않게 하려는 의도입니다. 디버깅용으로 켰다면 끝나고 다시 끄세요 |
| `Strict-Transport-Security` (180일) | 한 번 접속한 브라우저는 이 도메인을 평문 HTTP 로 열지 않습니다 |
| `-Server` | 서버 종류 노출 제거 |

> Caddy 는 기본적으로 Let's Encrypt 로 발급하고, 실패하면 다른 CA(ZeroSSL)로 넘어갈 수 있습니다.
> 펌웨어는 ISRG(Let's Encrypt) 루트만 신뢰하므로, 발급자가 바뀌면 ESP32 만 접속하지 못합니다.
> 이상하면 `curl -vI https://<도메인>.duckdns.org 2>&1 | grep -i issuer` 로 발급자를 확인하세요.

## 7. Google 로그인 설정

Google Cloud Console 에서 OAuth 클라이언트(**Web application**)를 만들고, **Authorized redirect URI** 를 정확히 아래 값으로 등록합니다.

```
https://<도메인>.duckdns.org/auth/callback
```

Client ID 와 Client secret 을 복사해 8절의 `.env` 에 넣습니다 (secret 은 생성 화면에서만 보입니다).
Audience 설정, 계정 제한 방식, 문제 해결은 **[google-oauth.md](google-oauth.md)** 에 단계별로 정리했습니다.

## 8. 서버 환경변수 (.env)

`~/eink-status-board/server/.env` 를 **600 권한**으로 만듭니다. 비밀 값 세 개(`SESSION_SECRET`, `API_KEY`, `DEVICE_TOKEN`)는 아래 명령이 자동으로 생성합니다.
`<도메인>`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS` 는 직접 채우세요.

먼저 비밀 값 세 개를 **생성해서 출력**합니다. `.env` 파일 안에서는 `$(...)` 같은 명령이 실행되지 않으므로,
명령어가 아니라 **출력된 값**을 넣어야 합니다. (공백·따옴표·괄호가 섞인 값이나 서로 같은 값은 서버가 시작을 거부합니다)

```bash
python3 -c 'import secrets; print("\n".join(f"{k}={secrets.token_urlsafe(32)}" for k in ("SESSION_SECRET","API_KEY","DEVICE_TOKEN")))'
```

출력된 세 줄을 복사해 두고 `.env` 를 만듭니다. `<...>` 자리표시자는 실제 값으로 바꿉니다.
(`nano` 가 `Error opening terminal` 로 실패하면 `TERM=xterm-256color nano .env` 로 실행하세요)

```bash
cd ~/eink-status-board/server
install -m 600 /dev/null .env
nano .env
```

```dotenv
SERVER_HOST=127.0.0.1
SERVER_PORT=5000
PUBLIC_BASE_URL=https://<도메인>.duckdns.org
GOOGLE_CLIENT_ID=<숫자>-<문자열>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<7절에서 복사한 값>
ALLOWED_EMAILS=you@gmail.com
SESSION_SECRET=<위에서 출력된 값>
API_KEY=<위에서 출력된 값>
DEVICE_TOKEN=<위에서 출력된 값 — 펌웨어의 DEVICE_TOKEN 과 같아야 함>
```

| 변수 | 설명 |
|------|------|
| `SERVER_HOST` | Caddy 뒤에서는 반드시 `127.0.0.1` — uvicorn 이 외부에서 직접 보이지 않게 합니다 |
| `PUBLIC_BASE_URL` | 공개 주소. 끝에 `/` 없이. OAuth Redirect URI 의 기준이며 `https://` 면 세션 쿠키에 `Secure` 가 붙습니다 |
| `ALLOWED_EMAILS` | 로그인을 허용할 Google 계정 (쉼표 구분) |
| `SESSION_SECRET` | 세션 쿠키 서명 키 (32자 이상) |
| `API_KEY` | Apple 단축어용. `/api/shortcuts/*` 에만 통합니다 (24자 이상) |
| `DEVICE_TOKEN` | ESP32 용. **펌웨어의 `DEVICE_TOKEN` 과 같은 값**이어야 합니다 (24자 이상) |

- `SERVER_RELOAD` 는 넣지 않습니다 (기본값 `false`). 운영에서 자동 재시작은 꺼 둡니다.
- `AUTH_DISABLED` 도 넣지 않습니다. 개발용 스위치가 남아 있으면 `PUBLIC_BASE_URL=https://...` 와 충돌해 서버가 시작을 거부합니다. `PUBLIC_BASE_URL` 은 `https://` 주소여야 합니다.
- 전체 변수 목록과 기본값은 [README 설정 절](../README.md#설정)에 있습니다.
- `.env` 에는 비밀이 들어 있습니다. 저장소에 커밋하지 마세요 (`.gitignore` 에 등록되어 있습니다).
- 단축어와 펌웨어에 넣을 값은 서버에서 확인합니다: `grep -E '^(API_KEY|DEVICE_TOKEN)=' ~/eink-status-board/server/.env`

## 9. systemd 서비스 등록

```bash
sudo nano /etc/systemd/system/eink-status-board.service
```

아래 내용에서 `<user>`를 리눅스 로그인 계정으로 바꿉니다.
3절에서 `~/eink-status-board` 안에 `.venv` 를 만들었으므로 경로가 아래와 같아야 합니다.

```ini
[Unit]
Description=E-ink Status Board FastAPI Server
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
Group=<user>
WorkingDirectory=/home/<user>/eink-status-board/server
ExecStart=/home/<user>/eink-status-board/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eink-status-board
sudo systemctl status eink-status-board
journalctl -u eink-status-board -f          # 실시간 로그
```

`main.py` 는 `server/.env` 를 직접 읽으므로 유닛에 `EnvironmentFile` 을 따로 둘 필요가 없습니다.
시작 로그에 `Google 로그인 허용 계정: 1개` 가 보이면 인증 설정이 로드된 것입니다.
`API_KEY 미설정` / `DEVICE_TOKEN 미설정` 경고가 보이면 `.env` 를 확인하세요.

> `main.py` 는 `SERVER_RELOAD=true` 일 때만 코드 변경 시 자동 재시작합니다. 운영에서는 켜지 마세요.
> 코드를 갱신했다면 [업데이트](#13-업데이트) 절차로 재시작합니다.

## 10. 방화벽 정리

서버가 `127.0.0.1` 에만 바인딩되므로 5000 포트는 이미 외부에서 닿지 않습니다. 예전에 5000 을 열어 둔 규칙은 **깊은 방어(defense in depth)** 차원에서 삭제합니다.
로컬 PC 나 Cloud Shell 에서 실행하세요.

```bash
gcloud compute firewall-rules list
gcloud compute firewall-rules delete allow-fastapi-5000     # 규칙 이름은 환경마다 다를 수 있습니다
```

**80/443 은 전 세계에 열어 둡니다.** 의도된 선택입니다.

- 셀룰러 환경의 iPhone 단축어는 출발 IP 가 계속 바뀝니다.
- Let's Encrypt 의 인증서 검증·갱신이 여러 IP 에서 들어옵니다.
- 접근 제어는 소스 IP 가 아니라 Google 로그인 / API 키 / 디바이스 토큰이 담당합니다. ([보안 모델](#15-보안-모델))

## 11. ESP32 펌웨어 연결

`eink-status-board.ino` 상단의 상수를 운영 서버에 맞춥니다. (`.ino.sample` 에서 복사한 파일, [README 빠른 시작](../README.md#2-펌웨어))

| 상수 | 값 |
|------|----|
| `WS_HOST` | `<도메인>.duckdns.org` |
| `WS_PORT` | `443` |
| `WS_PATH` | `/ws` |
| `WS_USE_TLS` | `true` |
| `DEVICE_TOKEN` | 서버 `.env` 의 `DEVICE_TOKEN` 과 같은 값 |

```bash
NO_UPLOAD=1 bash upload.sh     # 컴파일만 — 기기 없이 먼저 확인
bash upload.sh                 # USB 로 연결해 컴파일 + 업로드 (포트가 다르면 PORT=/dev/cu.usbserial-XXXX)
```

`upload.sh` 는 `.ino` 에 기본값(`YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, `SERVER_IP`, `YOUR_DEVICE_TOKEN`)이 남아 있으면 빌드를 거부합니다.

- 펌웨어는 `wss://` 로 접속하며 ISRG Root X1, X2, YE, YR 네 루트를 내장해 검증합니다. Let's Encrypt 갱신·체인 변경에는 펌웨어를 바꿀 필요가 없습니다.
- 현재 서버가 내려주는 체인: leaf ← Let's Encrypt `YE1` ← ISRG Root YE (ISRG Root X2 가 교차 서명) ← ISRG Root X2 (ISRG Root X1 이 교차 서명).
- 토큰이 틀리면 핸드셰이크 단계에서 HTTP 403 으로 거부되어 Admin 의 디바이스 목록에 나타나지 않습니다. 동시 연결이 이미 4개면 토큰이 맞아도 같은 방식으로 거부되고 서버 로그에 `디바이스 연결 한도` 가 남습니다.

## 12. 동작 검증 체크리스트

`<도메인>` 을 바꿔서 순서대로 확인합니다. 명령은 서버가 아닌 **내 PC** 에서 실행하는 것이 좋습니다 (외부에서 보이는 모습을 확인해야 하므로).

| # | 확인 | 명령 / 방법 | 기대 결과 |
|---|------|-------------|-----------|
| 1 | HTTPS 헬스 체크 | `curl -s https://<도메인>.duckdns.org/healthz` | `{"status":"ok"}` (`-k` 없이 성공 = 인증서 정상) |
| 2 | HTTP → HTTPS | `curl -sI http://<도메인>.duckdns.org/healthz \| head -n 1` | `308` 리다이렉트 |
| 3 | 무인증 차단 | `curl -s -o /dev/null -w '%{http_code}\n' https://<도메인>.duckdns.org/status` | `401` |
| 4 | 5000 포트 닫힘 (외부) | `nc -vz -w 5 <도메인>.duckdns.org 5000` | 타임아웃 (연결되면 방화벽 규칙이 남아 있는 것) |
| 5 | 5000 바인딩 (서버) | `ss -tlnp \| grep ':5000'` | `127.0.0.1:5000` (`0.0.0.0:5000` 이면 `SERVER_HOST` 확인) |
| 6 | API 키 | `curl -s -H "X-API-Key: <API_KEY>" https://<도메인>.duckdns.org/api/shortcuts/names` | 프리셋 이름 목록 (키가 틀리면 `401`) |
| 7 | 디바이스 토큰 거부 | `curl -s -o /dev/null -w '%{http_code}\n' --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://<도메인>.duckdns.org/ws` | `403` |
| 8 | 로그인 | 브라우저에서 `https://<도메인>.duckdns.org/admin` | Google 로그인 → Admin 열림, 내 이메일과 로그아웃 버튼 표시 |
| 9 | 허용되지 않은 계정 | 시크릿 창에서 `ALLOWED_EMAILS` 에 없는 계정으로 로그인 | "접근 권한 없음" (403) |
| 10 | 디바이스 연결 | Admin 의 디바이스 목록, `journalctl -u eink-status-board -f` | 디바이스가 "접속 중", 로그에 `디바이스 hello` |
| 11 | 화면 갱신 | Admin 에서 프리셋 적용 | 몇 초 안에 패널이 바뀜 |

## 13. 업데이트

```bash
cd ~/eink-status-board && git pull && ~/.local/bin/uv pip install --python .venv/bin/python -r server/requirements.txt && sudo systemctl restart eink-status-board
journalctl -u eink-status-board -f
```

- 자동 재시작(reload)은 운영에서 꺼 두므로 코드를 바꾸면 항상 재시작해야 합니다.
- 재시작해도 ESP32 가 알아서 다시 붙고, 서버는 `server/data/state.json` 으로 마지막 활성 프리셋을 복원합니다. 같은 프레임이면 패널은 다시 그려지지 않습니다.
- `.ino` 가 바뀐 업데이트는 서버 재시작만으로 반영되지 않습니다. USB 로 `bash upload.sh` 를 다시 실행해야 합니다.

**이전 버전(인증 없음, HTTP 5000)에서 올라오는 경우**

1. 위 업데이트 명령 (새 의존성 Authlib 등이 설치됩니다)
2. [Caddy](#6-caddy-https), [Google 로그인](#7-google-로그인-설정), [`.env`](#8-서버-환경변수-env) 설정. 인증 변수가 없으면 서버가 시작하지 않습니다.
3. systemd 유닛 이름이 `eink-status-board` 입니다. 예전 가이드의 `eink-board` 유닛을 쓰고 있었다면 `sudo systemctl disable --now eink-board` 로 끄고 [9절](#9-systemd-서비스-등록)의 새 유닛을 등록하세요. venv 가 예전처럼 `server/.venv` 라면 유닛의 `ExecStart` 만 실제 경로에 맞추면 됩니다.
4. [방화벽](#10-방화벽-정리)에서 5000 포트 규칙 삭제
5. **펌웨어 재업로드 필수** — 이전 펌웨어는 `X-Device-Token` 을 보내지 않고 `ws://:5000` 으로 접속하므로 더 이상 연결되지 않습니다
6. Apple 단축어의 URL 을 `https://` 로 바꾸고 `X-API-Key` 헤더 추가 ([apple-shortcuts.md](apple-shortcuts.md))

## 14. 백업과 복구

백업 대상은 두 가지입니다.

| 경로 | 내용 |
|------|------|
| `server/data/` | `presets.json`, `images/`, `state.json` (프리셋 전체 + 마지막 활성 프리셋 + 세션 세대 값 + 디바이스 이력). 손상 시 자동 보관된 `presets.json.corrupt-*` 도 이 폴더에 있음 |
| `server/.env` | 모든 비밀 값 (OAuth secret, SESSION_SECRET, API_KEY, DEVICE_TOKEN) |

```bash
cd ~/eink-status-board
(umask 077; tar czf ~/eink-backup-$(date +%F).tgz server/data server/.env)
# 내 PC 에서 가져오기
gcloud compute scp <인스턴스>:~/eink-backup-*.tgz . --zone <존>
```

`.env` 가 들어 있으므로 백업 파일은 암호화된 곳(비밀번호 관리자 등)에 보관하세요.
복구는 새 서버에서 3~9절을 마친 뒤 압축을 풀어 `server/data/` 와 `server/.env` 를 되돌리고 `sudo systemctl restart eink-status-board` 하면 됩니다.
Caddy 의 인증서는 백업하지 않아도 새 서버에서 자동으로 다시 발급됩니다.
오래된 백업을 복원하면 `state.json` 의 세션 세대 값도 그 시점 값으로 돌아가, 그 세대로 발급된 쿠키가 다시 유효해질 수 있습니다. 복원 뒤 로그인했다가 로그아웃을 한 번 하거나 `SESSION_SECRET` 을 바꾸세요 ([보안 모델](#15-보안-모델)).

## 15. 보안 모델

**누가 무엇을 할 수 있나**

| 자격 증명 | 쓰는 곳 | 접근 가능한 경로 | 유출되면 | 교체 방법 |
|-----------|---------|------------------|----------|-----------|
| Google 계정 (`ALLOWED_EMAILS`) | 브라우저 | 세션이 필요한 모든 경로 (Admin, 프리셋 생성·삭제·적용, 상태 조회) | 그 계정 탈취 = 전체 제어. Google 2단계 인증 권장 | `ALLOWED_EMAILS` 에서 제거 후 재시작 |
| 세션 쿠키 `eink_session` | 브라우저 | 위와 같음 (로그인 후 30일) | 쿠키를 가진 사람이 그 계정으로 전체 제어 | Admin 에서 로그아웃 (서버가 세션 세대 값을 새로 바꿔 그 전에 발급된 모든 세션을 폐기, 복사본 포함). 로그인할 수 없거나 "로그아웃 저장 실패" 가 떴다면 `SESSION_SECRET` 교체, 특정 계정은 `ALLOWED_EMAILS` 에서 제거 (둘 다 재시작 필요) |
| `SESSION_SECRET` | `.env` | 쿠키 서명 | 허용된 이메일로 쿠키를 위조해 전체 제어 가능 | 새 값 + 재시작 (모두 로그아웃) |
| `GOOGLE_CLIENT_SECRET` | `.env` | Google 인가 코드 교환 | 이것만으로는 로그인할 수 없음 (Google 계정 인증은 여전히 필요). 서버를 사칭하는 가짜 로그인 앱에 악용될 수 있음 | 콘솔에서 새 secret → `.env` → 재시작 → 이전 secret 비활성화 |
| `API_KEY` | `.env`, iPhone 단축어 | `/api/shortcuts/names`, `/api/shortcuts/activate` **만** | 프리셋 이름 조회, 프리셋 활성화(`force` 포함). 최악의 경우에도 표시 화면을 바꾸는 것까지이며, 패널 갱신은 `MIN_PUSH_INTERVAL`(3초)당 최대 한 번. 업로드·삭제·이름 변경·Admin·상태 조회는 불가 | 새 값 + 재시작 + 단축어의 헤더 갱신 |
| `DEVICE_TOKEN` | `.env`, 펌웨어 | `/ws` **만** | 최악의 경우: 프레임을 받아 볼 수 있음(현재 화면 내용), 동시 연결 4개 슬롯을 모두 점유해 정상 디바이스의 접속을 막을 수 있음, 디바이스 보고 필드(`fw`, `rssi` 등)를 써서 목록을 오염시킬 수 있음. 화면을 바꿀 수는 없음 | 새 값 + 재시작 + **펌웨어의 `DEVICE_TOKEN` 도 바꿔 USB 재업로드** |
| DuckDNS 토큰 | `~/duckdns/token.env` (600) | 도메인의 IP 변경 | 도메인을 가로채 인증서를 발급받고 API 키·디바이스 토큰을 훔칠 수 있음 | duckdns.org 에서 토큰 재생성 → `token.env` 갱신 |

**교체 절차**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"      # 새 값 생성
nano ~/eink-status-board/server/.env                                # 해당 변수 교체
sudo systemctl restart eink-status-board
```

- `API_KEY` 를 바꾸면 모든 단축어의 `X-API-Key` 값을 새 키로 고쳐야 합니다.
- `DEVICE_TOKEN` 을 바꾸면 펌웨어를 다시 올리기 전까지 디바이스가 접속하지 못합니다. 서버와 펌웨어를 같은 시점에 맞추세요.
- 키·토큰은 길이 24자 이상 무작위 값이어야 서버가 시작합니다. 비교는 상수 시간으로 이뤄집니다.

**알아 둘 점**

- 서버에는 로그인·API 키·토큰 시도에 대한 요청 속도 제한(rate limit)이 없습니다. 안전은 충분히 긴 무작위 비밀 값과 Google 인증에 의존합니다. 제한이 있는 것은 패널 보호용 프레임 전송 간격(`MIN_PUSH_INTERVAL`, 3초), 요청 본문 크기(JSON 16KB, 업로드 5MB), 동시 디바이스 연결(4개)뿐입니다.
- 비밀 값이 든 `.env`(600)와 `token.env`(600)는 저장소·채팅·스크린샷에 올리지 마세요.
- 상태를 바꾸는 API 는 모두 POST/PUT/PATCH/DELETE 이고, 세션 쿠키는 `SameSite=Lax` 라 다른 사이트에서 보낸 요청에는 실리지 않습니다.
- `AUTH_DISABLED=true` 는 로컬 개발 전용입니다. 운영 `.env` 에 넣지 마세요. `PUBLIC_BASE_URL=https://...` 와 함께 있으면 서버가 시작을 거부하도록 막아 두었습니다.
- 세션은 서버에 저장되지 않는 서명된 쿠키이고, 쿠키에는 발급 당시의 **세션 세대 값**(`gen`)이 들어 있습니다. 서버는 현재 세대 값(무작위 정수)을 메모리와 `state.json` 의 `session_generation` 에 두고, 쿠키의 값이 현재 값과 같을 때만 받아들입니다. 유효한 세션으로 로그아웃하면 세대 값이 새 무작위 값으로 바뀌어 그 전에 발급된 세션이 모두 폐기되므로, 쿠키가 유출됐다면 **로그인해서 로그아웃 한 번**이면 됩니다. 이 폐기는 허용된 모든 계정에 적용되며 시계에 의존하지 않습니다.
  이 방식은 fail closed 입니다. `state.json` 을 잃거나 깨지면 서버가 시작할 때 새 무작위 세대를 정하므로 옛 쿠키가 모두 거부되고 모두가 한 번 다시 로그인할 뿐입니다. 쿠키 주인이 로그인할 수 없거나 로그아웃이 `500` "로그아웃 저장 실패" 로 끝났다면 `SESSION_SECRET` 을 바꾸거나 `ALLOWED_EMAILS` 에서 그 주소를 뺀 뒤 서버를 재시작하세요.
  **오래된 백업으로 `server/data/` 를 복원하면** 옛 세대 값이 돌아와 그 세대로 발급된 쿠키가 다시 유효해질 수 있습니다. 복원 뒤에는 로그인했다가 로그아웃을 한 번 하거나 `SESSION_SECRET` 을 바꾸세요.

**알려진 한계**

- **서버 사칭**: 디바이스는 `WS_HOST` 에 대해 Let's Encrypt 가 발급한 인증서라면 무엇이든 신뢰하고 인증서 날짜는 검사하지 않습니다. 그래서 DuckDNS 계정을 장악한 사람은 서버를 사칭해 디바이스가 보내는 `DEVICE_TOKEN` 을 알아낼 수 있습니다. DuckDNS 로그인 수단(Google·GitHub 등)에 2단계 인증을 켜고, 의심되면 `DEVICE_TOKEN` 을 교체하세요.
- **물리 접근**: ESP32 에는 플래시 암호화·시큐어 부트가 없습니다. 기기를 손에 넣은 사람은 Wi-Fi 비밀번호와 `DEVICE_TOKEN` 을 읽을 수 있습니다.
- **연결 슬롯 점유**: `DEVICE_TOKEN` 을 가진 사람이 동시 연결 4개를 모두 차지하면 진짜 디바이스가 토큰을 교체할 때까지 접속하지 못합니다.
- **화면 변경**: `API_KEY` 를 가진 사람은 표시 화면을 바꿀 수 있습니다. 서버는 `MIN_PUSH_INTERVAL`(3초)당 한 번만 패널로 보내고, 펌웨어도 5초에 한 번(`MIN_REFRESH_INTERVAL_MS`)만 다시 그립니다.
- **`Content-Length` 필수**: 길이를 알 수 없는 요청(스트리밍 업로드, `curl -T`, `curl --data-binary @-`)은 `411` 로 거부됩니다. 브라우저·단축어·일반 `curl -d`/`-F` 는 영향이 없습니다.

## 16. 운영 팁

- **디스크**: 업로드 이미지는 `server/data/images/` 에 416×240 PNG 로 저장되므로 프리셋 하나당 수 KB 입니다. 프리셋은 최대 200개로 제한됩니다.
- **로그**: `journalctl -u eink-status-board --since today` 로 당일 접속·활성화·로그인 기록을 봅니다. 로그인 실패는 `허용되지 않은 계정`, `OAuth 실패` 로 검색하세요.
- **인증서**: Caddy 가 자동 갱신합니다. 문제가 있으면 `sudo journalctl -u caddy`. 80/443 이 닫히거나 DuckDNS 가 IP 를 못 따라오면 갱신이 실패합니다.
- **ESP32 재연결**: 서버를 재시작해도 펌웨어가 다시 접속합니다. 같은 프레임이면 패널은 깜빡이지 않습니다. 잔상이 남았으면 Admin 의 **강제 새로고침**을 쓰세요.
- **외부 모니터링**: 가동 확인은 인증이 필요 없는 `/healthz` 로 합니다. 내부 정보는 노출하지 않습니다.
- **상태 파일**: `server/data/state.json` 이 깨지거나 지워져도 서버는 빈 상태로 시작합니다 (프리셋은 `presets.json` 에 따로 있음). 서버가 시작할 때 새 무작위 세션 세대를 정하므로 옛 쿠키가 되살아나지는 않고 모두가 한 번 다시 로그인하면 됩니다. `state.json` 저장이 실패하면(디스크 가득 참 등) 로그에 `state.json 저장 실패` 만 남고 요청은 정상 처리됩니다.
- **손상된 `presets.json`**: 서버를 시작할 때 파일이 깨졌거나 형식이 맞지 않는 항목이 있으면, 원본을 `server/data/presets.json.corrupt-<유닉스 시각>` 으로 보관하고 유효한 항목만 남긴 채 시작합니다. 로그에 `presets.json 이 손상` 이 남으니 원본에서 필요한 항목을 수동으로 되살리세요. 데이터 문제로 서버가 못 뜨는 일은 없습니다.

## 17. 선택 사항

- **Tailscale**: PC·iPhone 을 Tailscale 에 넣고 Admin 은 사설망으로만 열 수도 있습니다. 다만 ESP32 는 Tailscale 을 쓸 수 없으므로 공개 443 은 그대로 필요합니다.
- **SSH 제한**: `default-allow-ssh` 규칙의 소스 범위를 내 공인 IP 로 좁히거나, [IAP TCP 전달](https://cloud.google.com/iap/docs/using-tcp-forwarding) 또는 OS Login 을 쓰세요.
- **자동 보안 업데이트**: `sudo apt install -y unattended-upgrades` 로 OS 패치를 자동 적용합니다.
