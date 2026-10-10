# Google 로그인 설정 (OAuth 클라이언트 만들기)

Admin 페이지는 Google 로그인으로 보호됩니다. Google 은 "이 사람이 누구인지"만 증명하고,
서버는 그 이메일이 `ALLOWED_EMAILS` 에 있을 때만 세션을 발급합니다.

이 문서는 Google Cloud Console 에서 OAuth 클라이언트를 **한 번** 만들어 `server/.env` 에 값을 채우는 절차입니다.
배포 전체 흐름은 [deploy-gcp.md](deploy-gcp.md), 환경변수 전체 목록은 [README](../README.md#설정) 를 참고하세요.

> 메뉴 이름은 현재 콘솔의 **Google Auth Platform** (Branding / Audience / Clients / Data Access) 기준이며
> 2026-10 에 Google 공식 문서([OAuth 클라이언트 관리](https://support.google.com/cloud/answer/15549257),
> [앱 대상 관리](https://support.google.com/cloud/answer/15549945),
> [웹 서버 앱 가이드](https://developers.google.com/identity/protocols/oauth2/web-server))로 확인했습니다.
> 콘솔 언어가 한국어면 메뉴 이름이 번역되어 보일 수 있습니다.

## 목차

1. [준비물](#1-준비물)
2. [프로젝트와 Google Auth Platform 시작](#2-프로젝트와-google-auth-platform-시작)
3. [Audience — 누가 로그인할 수 있나](#3-audience--누가-로그인할-수-있나)
4. [OAuth 클라이언트 만들기](#4-oauth-클라이언트-만들기)
5. [server/.env 에 넣기](#5-serverenv-에-넣기)
6. [접근 제한 방식](#6-접근-제한-방식)
7. [계정 추가·제거, 전체 로그아웃](#7-계정-추가제거-전체-로그아웃)
8. [로컬에서 로그인 테스트 (선택)](#8-로컬에서-로그인-테스트-선택)
9. [문제 해결](#9-문제-해결)

---

## 1. 준비물

| 항목 | 값 |
|------|----|
| 공개 주소 | `https://<도메인>.duckdns.org` — 서버의 `PUBLIC_BASE_URL` 과 같은 값 |
| Redirect URI | `https://<도메인>.duckdns.org/auth/callback` (`PUBLIC_BASE_URL` + `/auth/callback`) |
| Google 계정 | 콘솔에 로그인할 계정, 그리고 Admin 에 로그인할 계정 (같아도 됩니다) |

## 2. 프로젝트와 Google Auth Platform 시작

1. [Google Cloud Console](https://console.cloud.google.com/) 에서 프로젝트를 만들거나 고릅니다. VM 이 있는 프로젝트와 달라도 됩니다.
2. [Google Auth Platform](https://console.cloud.google.com/auth/overview) 개요 화면에서 **Get started** 를 누릅니다. (이미 설정된 프로젝트면 건너뜁니다)
3. 마법사를 순서대로 채웁니다.

   | 단계 | 입력 |
   |------|------|
   | App Information | **App name** `E-ink Status Board` 등 · **User support email** 본인 이메일 |
   | Audience | **External** |
   | Contact Information | 변경 알림을 받을 이메일 |
   | Finish | 정책 동의 후 **Create** |

Branding 의 로고·도메인 항목은 비워 둬도 됩니다. 이 앱은 검증이 필요한 스코프를 쓰지 않습니다.

## 3. Audience — 누가 로그인할 수 있나

왼쪽 메뉴 **Audience** 에서 User type 이 **External** 인지 확인하고, 아래 둘 중 하나를 고릅니다.

| 선택                      | 방법                                                            | 특징                                        |
| ----------------------- | ------------------------------------------------------------- | ----------------------------------------- |
| **Testing 유지** (개인용 권장) | **Test users** → **Add users** → 로그인할 Google 계정 입력 → **Save** | 최대 100명. 로그인 때 "확인되지 않은 앱" 경고가 뜨면 고급 → 계속 |
| **게시**                  | **Publish app** → 확인                                          | 경고 없이 로그인되는 것이 보통입니다                      |

- 이 서버는 `openid` 와 `email` 스코프만 요청합니다. 둘 다 민감하지 않은 기본 스코프라 **게시해도 Google 검증 심사가 필요 없습니다.**
  **Data Access** 메뉴에서 따로 스코프를 추가할 필요도 없습니다. 추가한다면 `openid` 와 `.../auth/userinfo.email` 만 넣으세요.
- 게시해도 아무나 들어오지 못합니다. 실제 문지기는 서버의 `ALLOWED_EMAILS` 이고, Google 쪽 Test users 는 그 앞단의 추가 필터일 뿐입니다.
- Testing 상태에서는 Google 이 발급한 토큰이 7일 뒤 만료되지만, 서버는 Google 토큰을 저장하지 않고 로그인 순간의 신원 확인에만 씁니다.
  이후에는 서버 자체 세션(30일)으로 동작하므로 영향이 없습니다.
  Google 문서에 따르면 기본 스코프만 쓰는 앱은 Test users 제한의 예외가 될 수도 있지만, 확실하게 하려면 계정을 추가해 두세요.

## 4. OAuth 클라이언트 만들기

1. 왼쪽 메뉴 **Clients** → **Create client**
2. 입력

   | 항목 | 값 |
   |------|----|
   | Application type | **Web application** |
   | Name | `status-board-server` 등 (콘솔 안에서만 보이는 이름) |
   | Authorized JavaScript origins | 비워 둠 (서버 측 흐름이라 필요 없음) |
   | Authorized redirect URIs | **Add URI** → `https://<도메인>.duckdns.org/auth/callback` |

3. **Create** 를 누르면 **Client ID** 와 **Client secret** 이 표시됩니다.

> **Client secret 은 이 화면에서만 볼 수 있습니다.** 이후 콘솔에는 끝 4자리만 보입니다.
> 지금 복사해 두거나 JSON 을 내려받으세요. 내려받은 `client_secret_*.json` 은 저장소 밖에 두세요 (`.gitignore` 가 막아 주지 않습니다).
> 잃어버렸다면 클라이언트 상세 화면에서 새 secret 을 추가하거나 클라이언트를 새로 만들어 `.env` 값을 바꿉니다.

**Redirect URI 규칙**

- 서버가 보내는 값과 **글자 단위로 같아야** 합니다: `https`/`http`, 호스트, 대소문자, 끝의 `/` 까지 모두 일치.
- 끝에 `/` 를 붙이지 않습니다: `https://<도메인>.duckdns.org/auth/callback`
- `http://` 는 `localhost` 만 허용됩니다. 그 외에는 `https://` 가 필수입니다.
- 설정을 바꾼 뒤 반영까지 몇 분 걸릴 수 있습니다.

## 5. server/.env 에 넣기

서버(VM)의 `~/eink-status-board/server/.env` 에 다섯 줄을 채웁니다. (나머지 변수는 [deploy-gcp.md](deploy-gcp.md#8-서버-환경변수-env))

```
PUBLIC_BASE_URL=https://<도메인>.duckdns.org
GOOGLE_CLIENT_ID=<숫자>-<문자열>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<4절에서 복사한 값>
ALLOWED_EMAILS=you@gmail.com
SESSION_SECRET=<무작위 32자 이상>
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # SESSION_SECRET 용
sudo systemctl restart eink-status-board
journalctl -u eink-status-board -n 20                            # "Google 로그인 허용 계정: 1개" 확인
```

브라우저에서 `https://<도메인>.duckdns.org/admin` 을 열면 Google 로그인으로 이동하고, 성공하면 Admin 이 열립니다.

## 6. 접근 제한 방식

```
브라우저 → / (메인 페이지의 "Google 계정으로 로그인" 버튼) → /login → Google 로그인 → /auth/callback → 서버가 신원 검증 → 허용 목록 확인 → 세션 쿠키 발급
```

1. 메인 페이지(`/`)의 버튼이 `/login` 으로 이어지고, `/login` 이 Google 로그인으로 보냅니다 (매번 계정 선택 화면을 띄웁니다). 메인 페이지를 바꿔도 Redirect URI(`/auth/callback`)는 달라지지 않습니다.
2. Google 이 `/auth/callback` 으로 인가 코드를 돌려주면, 서버가 state·nonce·PKCE(S256)·`id_token` 서명을 검증합니다.
3. 확인된 이메일(`email_verified` 가 true)이 **`ALLOWED_EMAILS` 에 있을 때만** 세션 쿠키 `eink_session` 을 발급합니다. 아니면 403 "접근 권한 없음" 페이지입니다.
4. 이후 **모든 요청마다** 세션의 이메일이 지금도 `ALLOWED_EMAILS` 에 있는지, 그리고 세션이 로그아웃으로 폐기되지 않았는지 다시 검사합니다. 목록에서 뺀 계정은 서버를 재시작하는 즉시 차단됩니다.

| 세션 쿠키 `eink_session` | 값 |
|------|----|
| 유효 기간 | 30일 |
| 속성 | `HttpOnly` · `SameSite=Lax` · `Secure` (`PUBLIC_BASE_URL` 이 `https://` 일 때) |
| 내용 | 로그인한 이메일과 발급 당시의 세션 세대 값. `SESSION_SECRET` 으로 **서명**되어 변조할 수 없지만 암호화는 아닙니다 |

API 키(단축어)와 디바이스 토큰(ESP32)은 이 로그인과 별개의 자격 증명입니다. 적용 범위는 [보안 모델](deploy-gcp.md#15-보안-모델)을 보세요.

## 7. 계정 추가·제거, 전체 로그아웃

| 하고 싶은 일 | 방법 |
|------|------|
| 계정 추가 | `ALLOWED_EMAILS=you@gmail.com,family@gmail.com` (쉼표 구분, 대소문자 무시) 후 재시작. Audience 가 Testing 이면 Test users 에도 추가 |
| 계정 제거 | 목록에서 빼고 재시작. 그 계정의 기존 세션도 다음 요청부터 거부됩니다 |
| **모두** 로그아웃 | Admin 의 로그아웃 버튼. 유효한 세션으로 로그아웃하면 서버가 **세션 세대 값**(무작위 정수)을 새 값으로 바꿔 `state.json` 에 저장하고, 그 전에 발급된 **모든 세션**(다른 브라우저·기기, 복사·유출된 쿠키, 다른 허용 계정 포함)이 더는 통하지 않게 됩니다. 다시 로그인하면 새 세대의 세션이 발급됩니다. 세션 없이 온 로그아웃 요청(교차 사이트 POST 등)은 아무것도 폐기하지 않습니다. 폐기를 디스크에 저장하지 못하면 "로그아웃 저장 실패"(500) 페이지가 뜨는데, 이 브라우저는 로그아웃됐어도 서버가 재시작되면 다른 기기의 로그인이 되살아날 수 있으니 `SESSION_SECRET` 을 바꾸세요 |
| 로그인할 수 없을 때의 전체 로그아웃 | `SESSION_SECRET` 을 새 값으로 바꾸고 재시작. 발급된 모든 쿠키가 무효가 됩니다. 쿠키 주인이 로그인할 수 없는 상황이거나 "로그아웃 저장 실패" 가 떴을 때, 오래된 백업을 복원했을 때의 대안입니다 |
| 특정 계정 차단 | `ALLOWED_EMAILS` 에서 빼고 재시작 |

> 세션 폐기는 시계가 아니라 무작위 세대 값의 일치로 판정하며 fail closed 입니다. `state.json` 을 잃거나 깨지면 서버가 시작할 때 새 무작위 세대를 정하므로 기존 쿠키가 모두 거부되고, 모두가 한 번 다시 로그인할 뿐입니다.
> 다만 **오래된 백업으로 `server/data/` 를 복원**하면 옛 세대 값이 돌아와 그 세대로 발급된 쿠키가 다시 유효해질 수 있습니다. 복원한 뒤에는 로그인했다가 로그아웃을 한 번 하거나 `SESSION_SECRET` 을 바꾸세요.
>
> 로그인 도중 Google 쪽 오류(400/502 페이지)가 나도 이미 로그인된 세션은 지워지지 않습니다. 허용되지 않은 계정으로 Google 로그인을 끝낸 경우(403)에만 기존 세션이 지워집니다.
| Client secret 교체 | Clients 에서 새 secret 발급 → `.env` 의 `GOOGLE_CLIENT_SECRET` 교체 → 재시작 → 콘솔에서 이전 secret 비활성화 |

```bash
sudo systemctl restart eink-status-board
```

## 8. 로컬에서 로그인 테스트 (선택)

로컬 개발은 보통 `AUTH_DISABLED=true` 로 충분합니다. 로그인 흐름 자체를 로컬에서 확인하려면:

1. 클라이언트의 Authorized redirect URIs 에 `http://localhost:5000/auth/callback` 을 **추가**합니다 (`localhost` 는 http 허용).
2. 로컬 `server/.env` 에 `PUBLIC_BASE_URL=http://localhost:5000` 과 나머지 인증 변수를 넣고, `AUTH_DISABLED` 없이 `python main.py` 를 실행합니다.
   서버는 `PUBLIC_BASE_URL` 로 `https://` 주소와 `http://localhost…`, `http://127.0.0.1…` 만 받아들입니다.
3. 주소창에는 `PUBLIC_BASE_URL` 에 적은 호스트와 똑같이 `http://localhost:5000` 을 쓰세요 (`127.0.0.1` 로 열면 호스트가 달라 로그인이 실패합니다).
4. 운영 `.env` 와 로컬 `.env` 를 섞지 마세요. `AUTH_DISABLED=true` 는 `PUBLIC_BASE_URL=https://...` 와 함께 쓰면 서버가 시작을 거부합니다.

## 9. 문제 해결

| 증상 | 원인 | 조치 |
|------|------|------|
| Google 화면 `Error 400: redirect_uri_mismatch` | 콘솔에 등록한 URI 와 서버가 보낸 URI(`PUBLIC_BASE_URL` + `/auth/callback`)가 다름. 끝의 `/`, `http`↔`https`, 도메인 오타, 대소문자가 흔한 원인 | 오류 화면의 요청 세부정보(request details)에서 `redirect_uri` 값을 확인해 Clients 의 URI 와 글자 단위로 맞추고, `.env` 의 `PUBLIC_BASE_URL` 도 확인. 수정 후 몇 분 기다렸다 재시도 |
| Google 화면 "액세스 차단됨: 앱이 테스트 중" (`access_denied`) | Audience 가 Testing 인데 이 계정이 Test users 에 없음 | Audience → Test users 에 계정 추가, 또는 **Publish app** |
| Google 화면 `invalid_client` | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` 오타, 다른 클라이언트의 값, 삭제된 secret | `.env` 값을 콘솔의 클라이언트와 대조하고 재시작 |
| 이 서버의 **"접근 권한 없음"** 페이지 (403) | Google 로그인은 성공했지만 이메일이 `ALLOWED_EMAILS` 에 없거나, Google 이 이메일을 미인증으로 판단 | 로그 `허용되지 않은 계정의 로그인 시도: '<이메일>'` 에서 Google 이 준 이메일을 확인해 `ALLOWED_EMAILS` 에 반영하고 재시작. 계정 선택 화면에서 다른 계정을 골랐는지도 확인 |
| 이 서버의 "로그인 실패" 페이지 (400) | state 불일치 — 로그인 도중 쿠키가 사라졌거나, `PUBLIC_BASE_URL` 과 다른 주소에서 시작했거나, 로그인 창을 너무 오래 둠. 또는 잘못된 client secret | `PUBLIC_BASE_URL` 과 같은 주소로 다시 로그인. 반복되면 로그의 `OAuth 실패: <코드>` 확인 |
| "로그인 실패 — Google 과 통신하는 중 문제" (502) | VM 이 Google 에 접속하지 못함 (DNS·아웃바운드 문제). `/login` 단계(Google 의 설정 문서 조회)와 `/auth/callback` 단계 모두 같은 페이지가 뜸 | VM 에서 `curl -sI https://accounts.google.com` 확인, 로그의 `Google 로그인 시작 실패` / `OAuth 처리 중 오류` 확인 |
| 서버가 시작되지 않고 로그에 **`인증 설정 누락: ...`** | 아래 변수 중 비어 있는 것이 있음: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`, `SESSION_SECRET`, `PUBLIC_BASE_URL`. 메시지가 비어 있는 이름을 알려줍니다 | `server/.env` 를 채우고 재시작. 로컬 개발이라면 `AUTH_DISABLED=true python main.py`. 같은 방식으로 `SESSION_SECRET` 32자 미만, `API_KEY`/`DEVICE_TOKEN` 24자 미만, `PUBLIC_BASE_URL` 형식 오류(`https://` 또는 로컬용 `http://localhost…`/`http://127.0.0.1…` 만 허용), 그리고 `AUTH_DISABLED=true` + `PUBLIC_BASE_URL=https://...` 조합도 시작을 거부합니다 |
| 로그인하면 다시 Google 로 돌아감 (루프) | 세션 쿠키가 저장되지 않음. 흔한 원인: `PUBLIC_BASE_URL` 은 `https://` 인데 `http://` 로 접속(Secure 쿠키 거부), 주소창 호스트와 `PUBLIC_BASE_URL` 호스트가 다름, 쿠키 차단 | `PUBLIC_BASE_URL` 과 똑같은 `https://` 주소로 접속. 시크릿 창에서 재시도 |

로그는 `journalctl -u eink-status-board -n 50` 로 확인합니다.
