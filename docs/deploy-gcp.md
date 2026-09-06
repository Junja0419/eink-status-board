# GCP 무료 티어 배포 가이드

GCP(Google Cloud Platform) e2-micro **Always Free** 인스턴스에 FastAPI 서버를 올리고,
DuckDNS로 도메인을 유지하며, systemd로 24시간 구동하는 절차입니다.

> ⚠️ **먼저 읽어주세요 — 보안 주의사항**
>
> 현재 서버에는 **인증이 없고 HTTP/ws 평문 통신**을 사용합니다.
> 5000 포트를 인터넷에 그대로 열면 누구나 프리셋을 만들고, 지우고, 디스플레이에 이미지를 띄울 수 있습니다.
> 아래 [보안 강화](#6-보안-강화-권장) 절을 반드시 함께 적용하세요.

## 목차

1. [인프라 요구사항](#1-인프라-요구사항)
2. [초기 시스템 설정](#2-초기-시스템-설정)
3. [Python 환경 구축 (uv)](#3-python-환경-구축-uv)
4. [DuckDNS 자동 갱신](#4-duckdns-자동-갱신)
5. [systemd 서비스 등록](#5-systemd-서비스-등록)
6. [보안 강화 (권장)](#6-보안-강화-권장)
7. [운영 팁](#7-운영-팁)

---

## 1. 인프라 요구사항

| 항목 | 값 | 비고 |
|------|----|------|
| Region | `us-central1` / `us-west1` / `us-east1` | Always Free 대상 리전 |
| Machine type | `e2-micro` | |
| Boot disk | **Standard Persistent Disk**, 30 GB 이하 | 🚨 SSD를 고르면 과금됩니다 |
| OS | Ubuntu 22.04 LTS 이상 | Debian도 동일 절차 |
| Firewall | TCP `5000` 인바운드 허용 | 가능하면 소스 IP를 제한하세요 (6절 참고) |

## 2. 초기 시스템 설정

```bash
sudo apt update && sudo apt install -y cron git curl
sudo systemctl enable --now cron
```

## 3. Python 환경 구축 (uv)

```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"   # 또는 새 셸 열기

# 소스 클론 — 아래 경로를 5절 systemd 유닛과 동일하게 유지하세요
git clone https://github.com/<본인_계정>/eink-status-board.git ~/eink-status-board
cd ~/eink-status-board/server

# 가상환경 + 의존성
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# (선택) 환경변수 파일
cat > .env <<'ENV'
SERVER_PORT=5000
# SERVER_HOST=127.0.0.1   # 6절의 Caddy 뒤에 둘 때는 외부 바인딩을 막는다
# SERVER_RELOAD=false     # 운영 환경에서는 false(기본값) 유지
ENV
```

동작 확인:

```bash
python main.py
# 다른 터미널에서
curl http://localhost:5000/status
```

## 4. DuckDNS 자동 갱신

인스턴스 재시작으로 외부 IP가 바뀌어도 도메인이 따라오도록 5분마다 갱신합니다.

```bash
mkdir -p ~/duckdns && cd ~/duckdns

# 토큰은 스크립트가 아니라 600 권한 파일에 따로 둔다
echo "DUCKDNS_TOKEN=[토큰]" > ~/duckdns/token.env && chmod 600 ~/duckdns/token.env

# [도메인]을 실제 값으로 바꿔서 실행 (-k 없이 인증서를 검증한다)
cat > ~/duckdns/duck.sh <<'SH'
#!/bin/sh
. ~/duckdns/token.env
curl -fsS -o ~/duckdns/duck.log \
  "https://www.duckdns.org/update?domains=[도메인]&token=${DUCKDNS_TOKEN}&ip="
SH
chmod 700 ~/duckdns/duck.sh

# 5분 주기 + 부팅 직후 1회
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "@reboot ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

## 5. systemd 서비스 등록

```bash
sudo nano /etc/systemd/system/eink-board.service
```

아래 내용에서 `<user>`를 리눅스 로그인 계정으로 바꿉니다.
3절에서 `~/eink-status-board/server` 안에 `.venv`를 만들었으므로 경로가 아래와 같아야 합니다.

```ini
[Unit]
Description=E-ink Status Board FastAPI Server
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
Group=<user>
WorkingDirectory=/home/<user>/eink-status-board/server
ExecStart=/home/<user>/eink-status-board/server/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eink-board
sudo systemctl status eink-board
journalctl -u eink-board -f          # 실시간 로그
```

> `main.py`는 `SERVER_RELOAD=true`일 때만 코드 변경 시 자동 재시작합니다.
> 운영 환경에서는 켜지 마세요. 코드를 갱신했다면 `git pull` 후 `sudo systemctl restart eink-board` 를 실행합니다.

## 6. 보안 강화 (권장)

우선순위 순입니다. 하나라도 적용하면 노출 면적이 크게 줄어듭니다.

1. **방화벽 소스 IP 제한** — GCP 방화벽 규칙에서 5000 포트의 소스 범위를 집/사무실 공인 IP와 iPhone 통신사 대역으로 제한합니다. ESP32는 같은 네트워크에 있으면 자동으로 포함됩니다.
2. **Tailscale 같은 사설망 사용** — 서버·PC·iPhone을 Tailscale에 넣고 5000 포트를 공개하지 않습니다. ESP32는 Tailscale을 직접 지원하지 않으므로, 이 경우 ESP32와 서버가 같은 LAN에 있거나 서브넷 라우터를 써야 합니다.
3. **Caddy 리버스 프록시로 HTTPS 적용** — DuckDNS 도메인으로 Let's Encrypt 인증서를 자동 발급받습니다. 이후 단축어는 `https://`로, ESP32는 `WebSocketsClient::beginSSL()` 로 접속하도록 바꿉니다.

   ```bash
   sudo apt install -y caddy
   sudo tee /etc/caddy/Caddyfile >/dev/null <<'CADDY'
   <도메인>.duckdns.org {
       reverse_proxy localhost:5000
   }
   CADDY
   sudo systemctl restart caddy
   ```
   이때 GCP 방화벽은 80/443만 열고 5000은 닫으며, `.env`에 `SERVER_HOST=127.0.0.1`을 넣어 uvicorn이 로컬에만 바인딩하게 합니다.
4. **API 키 인증 추가** — 서버에 `X-API-Key` 헤더 검증을 추가하면 단축어와 Admin에서 키만 넣어주면 됩니다. 현재 코드에는 포함되어 있지 않습니다. 필요하면 이슈로 남겨주세요.

## 7. 운영 팁

- **디스크 사용량**: 업로드 이미지는 `server/data/images/`에 416×240 PNG로 저장되므로 프리셋 하나당 수 KB입니다. e2-micro 30 GB에서는 사실상 걱정할 필요가 없습니다.
- **백업**: `server/data/` 폴더만 복사하면 프리셋 전체가 복원됩니다.
- **로그**: `journalctl -u eink-board --since today` 로 당일 접속·활성화 기록을 확인합니다.
- **ESP32 재연결**: 서버를 재시작해도 펌웨어가 5초 간격으로 재접속하며, 접속 직후 마지막 프레임을 다시 받아 화면을 복원합니다.
