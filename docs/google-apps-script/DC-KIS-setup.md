# Portfolio_KIS.gs 한국투자증권 KRX 현재가 설정

첨부 원본을 기반으로 만든 전체 교체 파일: `Portfolio_KIS.gs`.
기존 Google Sheets의 A2:E8(7행)을 그대로 읽습니다. 수량/평단을 코드에 저장하지 않습니다.
B열은 일반 텍스트로 설정하면 `0015B0`, `0082V0`를 보존할 수 있습니다.
현금(종목명 현금 또는 코드 0)은 API 호출 없이 수량 × 평단으로 평가합니다.

## 설치

1. 파일이 실제 `.xlsx`라면 Google 스프레드시트 형식으로 변환합니다.
2. 해당 시트의 확장 프로그램 → Apps Script에서 기존 DC.gs 내용을 이 DC.gs 전체로 교체합니다.
   기존 파일과 함께 추가하면 동일 함수명이 충돌합니다. 다른 파일에 같은 함수명이 있는지도 확인하세요.
3. 한국투자증권 Open API 서비스에서 실전용 App Key와 App Secret을 발급받습니다.
4. Apps Script 프로젝트 설정 → 스크립트 속성에 다음 값을 추가합니다.

| 속성 | 값 |
|---|---|
| KIS_APP_KEY | 실전 App Key |
| KIS_APP_SECRET | 실전 App Secret |
| TELEGRAM_TOKEN | 기존 값 유지 |
| TELEGRAM_CHAT_ID | 기존 값 유지 |
| DC_SHEET_NAME | 선택. 기본값 `퇴직연금DC` |
| DC_SPREADSHEET_ID | 선택. 독립 스크립트라면 필수. 시트 URL의 `/d/`와 `/edit` 사이 ID |

키를 코드나 셀에 넣지 마세요. DC_KIS_TOKEN 속성은 코드가 자동 생성합니다.
키/시크릿을 교체했다면 기존 DC_KIS_TOKEN 속성을 삭제한 뒤 테스트하세요.

## 실행 순서

1. `TEST_KIS_PRICES` 실행 후 권한 승인: 6종목의 KRX 현재가와 현금 값을 실행 로그에서 확인합니다. 주말에도 실행되며 텔레그램은 보내지 않습니다.
2. `PREVIEW_DC_PORTFOLIO` 실행: 기존 RSI/해외 지표까지 포함한 보고서를 로그로 확인합니다. 발송하지 않습니다.
3. 기존 `checkPortfolio` 시간 기반 트리거를 유지합니다. 트리거가 없다면 원하는 보고 주기로 이 함수를 등록합니다. 이 함수는 평일에 텔레그램을 발송하므로 1분 간격으로 등록하면 매분 메시지가 전송됩니다.

현재가는 checkPortfolio 실행 때마다 REST로 새로 조회합니다. 셀 갱신용 Z1은 이 보고서 방식에는 필요 없습니다.
시트 A:E는 입력값이며 실행 시 덮어쓰지 않습니다. TEST_KIS_PRICES의 fetchedAt은 요청 후 기록 시각이고 체결 시각이 아닙니다.
휴장일에도 평일이면 기존처럼 보고하며 가격은 마지막 시세일 수 있습니다.
API 조회 실패 시 평단으로 대체하지 않고 오류로 종료하여 해당 보고서는 발송하지 않습니다.
RSI는 네이버 일봉, NASDAQ/VIX는 Yahoo, 하이일드 스프레드는 FRED를 그대로 사용합니다.
이들의 기존 계산식 및 오류 처리는 이번 현재가 교체의 검증 범위가 아닙니다.

## API와 검증 범위

- 실전 서버: `https://openapi.koreainvestment.com:9443`
- 인증: `/oauth2/tokenP` (`client_credentials`)
- 현재가: `/uapi/domestic-stock/v1/quotations/inquire-price`
- TR: `FHKST01010100`, 시장: `FID_COND_MRKT_DIV_CODE=J` (KRX)
- 가격: `output.stck_prpr`
- 토큰은 만료 전까지 재사용하고 동시 발급은 ScriptLock으로 직렬화합니다.
- 현재가 요청은 같은 프로젝트 안에서 직렬화하며 이전 요청 종료 후 최소 1.1초 간격을 둡니다. 이는 보수적인 구현 간격이며 공식 한도 수치가 아닙니다.
- `EGW00201`은 초당 호출 한도 초과입니다. 이 오류 또는 JSON 응답의 HTTP 429이면 1.5초/3초/6초 대기 후 최대 3회 재시도합니다. 이 과정에서 토큰을 재발급하지 않습니다.
- 같은 키를 사용하는 다른 프로젝트/프로그램의 호출은 이 간격 제어에 포함되지 않습니다. 반복 발생하면 중복 실행을 확인하세요. 수정 후 DC.gs 전체를 Apps Script에 다시 붙여넣고 TEST_KIS_PRICES를 실행합니다. 기존 키/토큰 속성은 유지합니다.
- 로컬 모의 응답 검증만 수행했습니다. 실제 키로 인증/종목 조회 및 Google Apps Script 실행은 사용자의 설치 후 확인이 필요합니다.

공식 근거:
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/auth/auth_token/auth_token.py
