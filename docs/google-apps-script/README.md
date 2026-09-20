# Google Sheets 리밸런싱 알림 설정

이 문서는 Telegram으로 전략 알림을 받고 싶은 사용자가 Google Sheet와 Apps Script를 처음부터
설정하는 절차입니다. Cloudflare 배포는 운영자가 담당합니다. 먼저 전략을 고르고 알림의 의미를
확인하려면 [전략 사용자 안내](../strategy-user-guide.md)를 읽으세요.

## 시작 전에 받을 것과 준비할 것

운영자에게 다음 항목을 받습니다.

- `Code.gs` 파일
- 구독할 전략 ID
- `EVALUATION_API_URL`
- `EVALUATION_API_KEY`

사용자는 Google 계정과 Telegram 계정을 준비합니다. API 키와 Telegram 봇 토큰은 비밀번호처럼
취급하며, 스프레드시트 셀이나 채팅에 적지 않습니다.

## 1. Telegram 봇과 채팅 ID 준비

이미 본인 봇의 토큰과 채팅 ID가 있다면 이 단계를 건너뜁니다.

1. Telegram에서 공식 `@BotFather`를 열고 `/newbot`을 보냅니다.
2. 안내에 따라 이름과 사용자 이름을 정한 뒤 발급된 봇 토큰을 안전하게 보관합니다.
3. 새로 만든 봇과의 대화를 열어 `/start`를 보냅니다.
4. 아래 주소의 `<봇_토큰>`을 발급받은 값으로 바꿔 브라우저에서 엽니다.

   ```text
   https://api.telegram.org/bot<봇_토큰>/getUpdates
   ```

5. 응답에서 방금 보낸 메시지의 `message.chat.id` 값을 찾아 채팅 ID로 보관합니다. 토큰이 든
   주소나 응답 화면을 다른 사람에게 보내지 않습니다.

봇 생성과 토큰 발급은 [Telegram 공식 봇 안내](https://core.telegram.org/bots/tutorial),
`getUpdates` 응답 형식은 [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)를
참고하세요. `getUpdates` 결과가 비어 있으면 봇에 메시지를 하나 더 보낸 뒤 다시 확인합니다.

## 2. 스프레드시트와 Apps Script 만들기

1. 새 Google 스프레드시트를 만듭니다.
2. 스프레드시트에서 **확장 프로그램 → Apps Script**를 엽니다.
3. 편집기에 있는 기존 코드를 모두 지우고 운영자에게 받은 `Code.gs` 전체를 붙여 넣습니다.
4. 저장합니다.

이 경로로 만든 Apps Script는 현재 스프레드시트에 연결됩니다. 별도의 독립형 Apps Script
프로젝트를 만들 필요가 없습니다.

## 3. 비밀값을 스크립트 속성에 저장

Apps Script 왼쪽의 **프로젝트 설정 → 스크립트 속성**에서 다음 네 속성을 추가합니다.

| 속성 | 입력할 값 |
|---|---|
| `EVALUATION_API_URL` | 운영자가 전달한 `/notification-evaluations` 주소 |
| `EVALUATION_API_KEY` | 운영자가 별도로 전달한 API 키 |
| `TELEGRAM_BOT_TOKEN` | 1단계에서 받은 본인 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 1단계에서 확인한 본인 채팅 ID |

`SPREADSHEET_ID`는 이 안내처럼 스프레드시트에서 Apps Script를 연 경우에는 입력하지 않습니다.
독립형 Apps Script를 사용해야 하는 경우에만 스프레드시트 URL의 `/d/`와 `/edit` 사이 문자열을
`SPREADSHEET_ID`로 추가합니다.

## 4. 알림용 시트 만들기

Apps Script 상단의 함수 선택 목록에서 `setupSpreadsheet`를 선택하고 **실행**을 누릅니다.
처음 실행할 때 Google 권한 확인 화면이 나오면 현재 스프레드시트 수정, 외부 API 호출과 트리거
생성에 필요한 권한을 검토한 뒤 허용합니다.

실행이 끝나면 스프레드시트에 다음 탭이 생깁니다.

| 탭 | 용도 | 사용자가 수정하는 열 |
|---|---|---|
| `알림 전략` | 구독할 전략 선택 | `알림`, `전략 ID`, `전략명` |
| `개인 전략` | 공개하지 않을 YAML 저장 | `전략 ID`, `전략 YAML` |
| `알림 이력` | 발송된 메시지 기록 | 수정하지 않음 |
| `실행 로그` | 성공·실패와 오류 확인 | 수정하지 않음 |

## 5. 전략 선택

### 공개 전략을 구독할 때

`알림 전략` 탭의 새 행에 다음처럼 입력합니다.

- `알림`: 체크
- `전략 ID`: 운영자에게 받은 전략 ID
- `전략명`: 알아보기 쉬운 이름, 생략 가능

### 개인 YAML 전략을 구독할 때

1. `개인 전략` 탭 A열에 YAML의 `strategy.id`와 같은 전략 ID를 입력합니다.
2. 같은 행 B열에 YAML 전체를 붙여 넣습니다.
3. `알림 전략` 탭에도 같은 전략 ID를 입력하고 `알림`을 체크합니다.

개인 전략 ID는 공개 전략 ID와 겹치지 않아야 합니다. Apps Script는 체크한 개인 전략의 YAML만
인증된 평가 요청에 포함하고, Worker는 그 YAML을 Pages나 KV에 저장하지 않습니다.

`source`와 `products`를 사용하는 상품연결 전략은 원본 전략과 별도의 구독입니다. 같은 계좌에서
원본과 상품연결 전략을 함께 체크하면 같은 신호가 두 번 올 수 있으므로 실제 운용할 전략 ID
하나만 선택합니다.

## 6. 전략 상태 초기화

함수 목록에서 `initializeNotificationStates`를 선택해 한 번 실행합니다. 이 작업은 선택한 전략의
최근 상태를 Worker KV에 만들며, 실제 주문 알림을 발송하지 않습니다.

다음 경우에도 이 함수를 다시 실행합니다.

- 새 전략을 체크한 경우
- 개인 전략 YAML의 계산 규칙을 바꾼 경우
- 운영자가 공개 전략의 계산 규칙이 바뀌었다고 안내한 경우

## 7. Telegram 연결과 메시지 모양 확인

1. `sendTestMessage()`를 실행해 짧은 연결 확인 메시지를 받습니다.
2. `sendScheduledSummaryTest()`를 실행해 현재 시장 기준의 정기 브리핑 모양을 확인합니다.

두 번째 테스트는 선택한 전략이 있어야 동작합니다. 테스트는 Worker의 알림 상태를 바꾸지
않으므로 실제 사건 알림을 놓치게 하지 않습니다.

## 8. 자동 알림 시작

`installTriggers()`를 한 번 실행합니다. 기존 `runNotificationCheck` 트리거가 있으면 교체하므로
여러 번 실행해도 10분 트리거가 중복 생성되지 않습니다.

설정이 끝났는지 다음 항목을 확인합니다.

- Telegram 테스트 메시지를 받았다.
- `알림 전략`에서 사용할 전략만 체크했다.
- `initializeNotificationStates()`가 오류 없이 끝났다.
- Apps Script 왼쪽의 **트리거** 화면에 `runNotificationCheck`가 하나 있다.

트리거는 10분마다 실행되지만 실제 평가 API 호출은 KST 07:00~10:00에만 합니다. 알림을 잠시
끄려면 `알림 전략`의 체크를 해제합니다. 자동 실행 자체를 중단하려면 Apps Script의 **트리거**
화면에서 `runNotificationCheck` 트리거를 삭제합니다.

## 알림 종류와 기록

- `리밸런싱 실행`: 실제 주문 검토가 필요한 목표 비중과 현재 대비 증감
- `사전주의`: 전략에서 지정한 주요 상태 변화·확인 시작 또는 목표 괴리 경고
- `정기 시장 브리핑`: 전략의 `notifications.schedule`에 따른 시장 상태 요약

같은 시장 기준일의 브리핑과 사건 알림은 한 메시지로 합칩니다. 발송 내용은 `알림 이력`, 실행
성공·실패는 `실행 로그`에서 확인합니다.

## 코드가 업데이트된 경우

1. Apps Script 편집기의 기존 코드를 새 `Code.gs` 전체로 교체하고 저장합니다.
2. `setupSpreadsheet()`를 실행해 새 열이 필요하면 추가합니다. 기존 기록은 유지됩니다.
3. `sendTestMessage()`로 연결을 확인합니다.
4. 트리거 변경 안내가 있었다면 `installTriggers()`를 다시 실행합니다.

스크립트 속성은 코드를 교체해도 유지됩니다. 전략 계산 규칙도 함께 바뀌었다면
`initializeNotificationStates()`를 다시 실행합니다.

## 문제 해결

| 증상 | 확인할 내용 |
|---|---|
| Telegram 메시지가 오지 않음 | 봇에 `/start`를 보냈는지, 토큰과 채팅 ID가 맞는지 확인 |
| `401` 또는 `unauthorized` | `EVALUATION_API_KEY`가 운영자의 현재 키와 같은지 확인 |
| 선택한 전략이 없다는 오류 | `알림 전략` 탭의 체크박스와 전략 ID 확인 |
| 개인 전략 오류 | A열 ID가 YAML의 `strategy.id`와 같은지, B열에 YAML 전체가 있는지 확인 |
| 정기 실행이 안 됨 | Apps Script **트리거** 화면에 `runNotificationCheck`가 하나 있는지 확인 |
| 그 밖의 오류 | `실행 로그`의 상세 내용과 Apps Script **실행** 화면의 오류를 운영자에게 전달 |

API 키, 봇 토큰, 개인 전략 YAML은 오류 화면을 전달할 때도 가려야 합니다.
