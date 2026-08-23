# Google Sheets 리밸런싱 알림

1. 새 Google Sheet에서 **확장 프로그램 → Apps Script**를 열고 `Code.gs`를 붙여 넣습니다.
2. 프로젝트 설정의 Script Properties에 아래 값을 설정합니다.

```text
EVALUATION_API_URL=https://YOUR_WORKER.workers.dev/notification-evaluations
EVALUATION_API_KEY=Cloudflare Worker secret과 동일한 값
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
# 스프레드시트에 바인딩하지 않은 독립형 Apps Script인 경우에만 설정
SPREADSHEET_ID=스프레드시트 ID
```

독립형 Apps Script라면 스프레드시트 URL의 `/d/`와 `/edit` 사이 문자열을
`SPREADSHEET_ID`에 입력합니다. 스프레드시트에서 **확장 프로그램 → Apps Script**로
열었다면 이 값은 필요하지 않습니다.

3. `setupSpreadsheet()`를 한 번 실행한 뒤 `알림 전략` 시트에 전략 ID를 입력하고 알림을 체크합니다.
4. `sendTestMessage()`로 Telegram 연결을 확인한 뒤 `installTriggers()`를 한 번 실행합니다.

10분 트리거는 KST 07:00~10:00에만 평가 API를 호출합니다. Cloudflare Worker가 새 시장 데이터를 병합하고 전략을 평가하며, 스프레드시트는 발송 이력으로 중복 메시지를 막습니다.

## 개인 전략

외부에 공개하지 않을 YAML 전략은 `개인 전략` 시트에 넣습니다. A열에는 YAML의
`strategy.id`와 같은 전략 ID를, B열에는 YAML 전체를 붙여 넣습니다. 그리고 같은 ID를
`알림 전략` 시트에도 입력하고 알림을 체크합니다.

알림을 실행할 때 Apps Script는 선택된 개인 전략의 YAML만 인증된 Worker 요청에 포함합니다.
Worker는 이를 KV나 Pages에 저장하지 않고 해당 요청의 전략 계산에만 사용합니다. 따라서
전략 YAML을 공개 Pages나 Git 저장소에 올릴 필요가 없습니다. 개인 전략 ID는 공개 전략 ID와
중복될 수 없습니다.

## 무료 플랜 최초 초기화

Cloudflare 무료 플랜에서는 전체 과거 이력 계산을 Worker에서 반복하지 않습니다. `알림 전략`
시트에서 전략을 체크한 뒤 Apps Script의 `initializeNotificationStates()`를 한 번 실행하세요.
Worker가 최근 계산 구간으로 전략 상태를 만들고 KV에 저장합니다. 이후에는 Worker가 저장된
상태와 새 거래일만 계산합니다.

전략 YAML 또는 계산 규칙을 변경했다면 같은 함수를 다시 실행해 해당 전략 상태를 초기화합니다.
`scripts/seed-notification.mjs`는 Apps Script를 사용할 수 없는 경우에만 쓰는 고급 로컬 도구입니다.
