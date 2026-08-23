# 온라인 데이터 프록시

`data-proxy`는 선택적으로 배포하는 Cloudflare Worker입니다. 오프라인 묶음에
포함되지 않은 종목의 일별 가격 이력을 독립형 브라우저 앱이 가져올 수 있게 합니다.
사용자 PC나 휴대폰에 설치되는 프로그램은 아닙니다.

Worker는 다음 두 엔드포인트를 제공합니다.

```text
GET /prices?tickers=QQQ,BND&start=2010-01-01&end=2026-08-15
```

이 API는 일별 OHLCV 데이터를 정규화하여 반환합니다. 12시간 엣지 캐시를 적용하고,
잘못된 종목 코드 및 지나치게 큰 요청 범위는 거부하며 CORS 헤더를 제공합니다.
브라우저는 받아 온 행을 IndexedDB에 저장해 이후 오프라인에서도 활용합니다. 전략
정의와 사용자 데이터는 이 프록시로 전송하지 않습니다.

`POST /notification-evaluations`는 Google Apps Script 알림 컨트롤러 전용의 인증된
엔드포인트입니다. 요청된 전략만 읽고, `MARKET_DATA` KV 네임스페이스에 저장된 종목
데이터의 새 구간만 갱신한 뒤 리밸런싱 이벤트를 반환합니다. 배포 전에는
`STRATEGY_MANIFEST_URL` 설정, KV 네임스페이스 생성,
그리고 `NOTIFICATION_API_KEY` Worker 비밀값 등록이 필요합니다. Apps Script 예제는
`docs/google-apps-script/`에 있습니다.

## 배포 방법

1. Cloudflare 계정을 만들거나 로그인합니다.
2. 정적 사이트를 먼저 배포하고 `strategies/manifest.json`의 URL을 확인합니다.
3. `data-proxy` 디렉터리에서 `npm install`을 실행한 뒤 `npx wrangler login`으로 로그인합니다.
4. `npx wrangler kv namespace create MARKET_DATA`를 실행합니다. 출력된 ID를
   `wrangler.toml`에 입력하고, `STRATEGY_MANIFEST_URL`을 2단계에서 확인한 URL로 바꿉니다.
5. `npx wrangler secret put NOTIFICATION_API_KEY`를 실행해 충분히 긴 무작위 값을 입력합니다.
6. `npx wrangler deploy`를 실행합니다.
7. 출력된 Worker URL을 오프라인 앱의 데이터 프록시 설정에 입력하거나, HTML 묶음을
   생성할 때 전달합니다.

Google Sheets의 `EVALUATION_API_URL`에는 다음 값을 넣습니다.

```text
https://YOUR_WORKER.workers.dev/notification-evaluations
```

`EVALUATION_API_KEY`에는 Worker의 `NOTIFICATION_API_KEY`와 같은 값을 넣습니다.

기본 공급자는 Yahoo Finance의 chart 엔드포인트입니다. 공급자의 제공 여부와 이용 조건은
변경될 수 있습니다. 공급자별 처리는 `loadTicker` 함수로 분리되어 있으므로, 브라우저
애플리케이션을 바꾸지 않고 다른 공급자로 교체할 수 있습니다.
