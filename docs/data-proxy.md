# Cloudflare 데이터 프록시 및 배포

이 문서는 정적 전략 사이트와 `data-proxy` Cloudflare Worker를 배포·운영하는 서버 운영자를
위한 안내입니다. 전략을 작성하는 방법은 [YAML 전략 안내](strategy.md), 알림을 받는 방법은
[전략 사용자 안내](strategy-user-guide.md)를 참고하세요.

## 구성과 역할

| 구성 | 배포 대상 | 역할 |
|---|---|---|
| `dist/web/` | Cloudflare Pages | 전략 검토용 정적 사이트와 공개 전략 YAML 제공 |
| `data-proxy/` | Cloudflare Workers | 가격 조회와 인증된 알림 평가 API 제공 |
| `MARKET_DATA` | Workers KV | 시장 데이터와 전략별 알림 상태 저장 |

Worker는 다음 엔드포인트를 제공합니다.

- `GET /prices`: 일별 OHLCV 데이터를 정규화해 반환합니다. 12시간 엣지 캐시를 적용하고,
  잘못된 종목 코드와 지나치게 큰 요청 범위를 거부합니다.
- `POST /notification-evaluations`: Google Apps Script가 사용하는 인증된 평가 API입니다.
- `POST /notification-bootstrap`: 처음 구독하거나 계산 규칙이 바뀐 전략의 알림 상태를
  초기화합니다.

브라우저는 `/prices`에서 받은 행을 IndexedDB에 저장해 다시 사용합니다. 공개 전략 사이트를
사용할 때는 전략 정의가 Pages에 게시되지만, 사용자가 스프레드시트에 넣은 개인 전략 YAML과
사용자 데이터는 Pages나 KV에 저장하지 않습니다.

## 배포 전에 정할 값

아래 자리표시자는 실제 값으로 바꿔 사용합니다.

| 자리표시자 | 예시 | 설명 |
|---|---|---|
| `<PAGES_PROJECT_NAME>` | `investment-strategy` | Pages 프로젝트 이름 |
| `<PAGES_BASE_URL>` | `https://investment-strategy.pages.dev` | 배포된 정적 사이트 주소 |
| `<WORKER_BASE_URL>` | `https://investment-strategy-data-proxy.example.workers.dev` | 배포된 Worker 기본 주소 |

필요한 로컬 도구는 Python 3.11 이상, [uv](https://docs.astral.sh/uv/), Node.js와 npm입니다.
Wrangler의 Pages 직접 업로드 방식은 빌드 결과 폴더를 그대로 배포합니다. Git 연동 방식과
직접 업로드 방식 사이의 전환에는 제약이 있으므로, 이 프로젝트에서는 아래의 직접 업로드
절차를 기준으로 설명합니다. 자세한 제약은
[Cloudflare Pages Direct Upload 안내](https://developers.cloudflare.com/pages/get-started/direct-upload/)를
확인하세요.

## 최초 구축

### 1. 정적 사이트를 먼저 배포

저장소 루트에서 의존성을 설치하고 첫 정적 사이트를 만듭니다. 이 첫 빌드는 아직 Worker 주소가
없으므로 `--data-proxy`를 생략합니다.

```powershell
uv sync
uv run python src/legacy-python/offline_export.py --static-site dist/web
```

이어서 `data-proxy` 디렉터리에서 Wrangler를 설치하고 Cloudflare에 로그인한 뒤 Pages
프로젝트를 만들고 배포합니다.

```powershell
cd data-proxy
npm install
npx wrangler login
npx wrangler pages project create <PAGES_PROJECT_NAME>
npx wrangler pages deploy ../dist/web --project-name <PAGES_PROJECT_NAME> --branch main
```

프로젝트 생성 중 프로덕션 브랜치를 묻는 경우 `main`을 선택합니다.

명령이 출력한 Pages 주소를 열어 사이트가 표시되는지 확인하고, 다음 주소가 JSON을 반환하는지
확인합니다.

```text
<PAGES_BASE_URL>/strategies/manifest.json
```

### 2. KV와 Worker 설정

`data-proxy` 디렉터리에서 KV 네임스페이스를 한 번만 만듭니다.

```powershell
npx wrangler kv namespace create MARKET_DATA
```

출력된 ID를 `data-proxy/wrangler.toml`의 `[[kv_namespaces]]` 아래 `id`에 넣습니다. 같은 파일의
`STRATEGY_MANIFEST_URL`은 다음처럼 앞 단계의 Pages 주소로 바꿉니다.

```toml
[vars]
STRATEGY_MANIFEST_URL = "<PAGES_BASE_URL>/strategies/manifest.json"
```

알림 API 키는 충분히 긴 무작위 값으로 만들고 저장소나 `wrangler.toml`에 기록하지 않습니다.
다음 명령을 실행한 뒤 대화형 입력란에 값을 입력합니다.

```powershell
npx wrangler secret put NOTIFICATION_API_KEY
npm test
npx wrangler deploy
```

배포 결과에 표시된 `<WORKER_BASE_URL>`을 기록합니다. 다음 주소가 가격 JSON을 반환하면 Worker가
정상 동작하는 것입니다.

```text
<WORKER_BASE_URL>/prices?tickers=QQQ&start=2026-01-01
```

### 3. Worker 주소를 넣어 정적 사이트를 다시 배포

사이트가 온라인 가격 프록시를 사용하도록 저장소 루트에서 다시 빌드합니다.

```powershell
cd ..
uv run python src/legacy-python/offline_export.py --static-site dist/web --data-proxy <WORKER_BASE_URL>
cd data-proxy
npx wrangler pages deploy ../dist/web --project-name <PAGES_PROJECT_NAME> --branch main
```

사이트를 새로 열어 전략 목록이 표시되고 온라인 데이터 갱신이 되는지 확인합니다. 기존 브라우저
탭이 이전 파일을 들고 있으면 강력 새로고침을 사용합니다.

### 4. 알림 사용자에게 전달할 값

알림 설정 사용자에게는 다음 네 가지를 안전한 경로로 전달합니다.

- 전략 사이트 주소
- `docs/google-apps-script/Code.gs`
- `EVALUATION_API_URL`: `<WORKER_BASE_URL>/notification-evaluations`
- `EVALUATION_API_KEY`: Worker의 `NOTIFICATION_API_KEY`와 같은 값

API 키를 문서, 저장소, 스프레드시트 셀 또는 공개 채팅에 넣지 않습니다.

## 내용 변경 후 업데이트

### 전략 YAML 또는 웹 화면을 바꾼 경우

`strategies/`, `strategies/manifest.json`, `src/web/` 또는 정적 사이트 생성 코드를 바꿨다면
정적 사이트를 다시 만든 뒤 같은 Pages 프로젝트에 재배포합니다.

```powershell
uv run python src/legacy-python/offline_export.py --static-site dist/web --data-proxy <WORKER_BASE_URL>
cd data-proxy
npx wrangler pages deploy ../dist/web --project-name <PAGES_PROJECT_NAME> --branch main
```

`dist/web/`을 직접 수정하면 다음 빌드에서 덮어써지므로 원본인 `strategies/` 또는 `src/web/`을
수정해야 합니다. Worker는 공개 전략 목록과 YAML을 최대 5분 동안 캐시하므로, 새 전략이 알림
평가에 반영되기까지 잠시 걸릴 수 있습니다. 전략의 계산 규칙이 바뀌었다면 알림 사용자는
Apps Script에서 `initializeNotificationStates()`를 다시 실행해야 합니다.

### Worker 코드를 바꾼 경우

`data-proxy/src/` 또는 `data-proxy/wrangler.toml`을 바꿨다면 테스트 후 Worker만 다시
배포합니다. 기존 KV 데이터와 비밀값은 새로 만들거나 다시 입력하지 않습니다.

```powershell
cd data-proxy
npm test
npx wrangler deploy
```

`STRATEGY_MANIFEST_URL`이나 KV 바인딩을 바꾼 경우에는 배포 뒤 가격 API와 알림 테스트를 모두
확인합니다.

### Apps Script 코드를 바꾼 경우

`docs/google-apps-script/Code.gs`만 바꿨다면 Pages나 Worker를 다시 배포할 필요가 없습니다.
알림 사용자가 Apps Script의 기존 코드를 새 코드로 교체하고 `setupSpreadsheet()`를 실행해
시트 열을 갱신하도록 안내합니다.

### API 키를 교체한 경우

Worker에서 새 비밀값을 입력한 뒤 모든 알림 사용자의 Apps Script 스크립트 속성
`EVALUATION_API_KEY`도 같은 값으로 바꿉니다.

```powershell
cd data-proxy
npx wrangler secret put NOTIFICATION_API_KEY
```

한쪽만 바꾸면 알림 평가 요청이 `401`로 실패합니다.

## 배포 확인과 되돌리기

Pages 배포 목록은 다음 명령으로 확인합니다.

```powershell
npx wrangler pages deployment list --project-name <PAGES_PROJECT_NAME>
```

운영 확인 순서는 다음과 같습니다.

1. Pages의 `/strategies/manifest.json`이 열리는지 확인합니다.
2. Worker의 `/prices`가 가격 JSON을 반환하는지 확인합니다.
3. 테스트용 Google Sheet에서 `sendTestMessage()`와 `sendScheduledSummaryTest()`를 실행합니다.
4. Cloudflare 대시보드의 Worker 로그와 Apps Script의 `실행 로그`를 확인합니다.

Worker 배포에 문제가 생기면 `npx wrangler versions list`로 버전을 확인하고
`npx wrangler rollback`으로 직전 버전으로 되돌릴 수 있습니다. Pages는 Cloudflare
대시보드의 배포 목록에서 정상 배포를 다시 프로덕션으로 승격할 수 있습니다.

### Windows에서 명령이 실행되지 않을 때

- PowerShell 실행 정책이 `npm.ps1` 또는 `npx.ps1`을 막으면 같은 명령에서 `npm`을
  `npm.cmd`로, `npx`를 `npx.cmd`로 바꿉니다.
- `uv`가 사용자 캐시 폴더 권한 오류를 내면 저장소 루트에서
  `$env:UV_CACHE_DIR="$PWD\.uv-cache"`를 실행한 뒤 빌드 명령을 다시 실행합니다.

## 데이터 공급자 유의 사항

기본 공급자는 Yahoo Finance의 chart 엔드포인트입니다. 공급자의 제공 여부와 이용 조건은 바뀔
수 있습니다. 공급자별 처리는 `loadTicker` 함수로 분리되어 있어 브라우저 애플리케이션을
바꾸지 않고 다른 공급자로 교체할 수 있습니다.
