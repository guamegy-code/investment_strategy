# 선언형 전략 DSL v1

YAML 파일로 자산배분 규칙과 리밸런싱 조건을 정의한다.

## 심플 예제

```yaml
strategy:
  id: retirement-7030-band
  name: Retirement 70/30 Band
  version: 1
  dsl_version: 1
  enabled: true

assets:
  required: [QQQ, BND]
  risk: [QQQ]

target:
  - weights:
      QQQ: 70%
      BND: 30%

rebalance:
  - check: daily
    when: target_deviation() >= 5%

execution:
  days: 1
```

`strategies` 디렉터리의 기본 YAML은 정적 웹페이지에서 읽는다. 사용자가 가져온 YAML은
브라우저에만 저장되며, 개인 알림 전략은 Google 스프레드시트에 별도로 등록한다. 파일명은
실행 의미에 영향을 주지 않으며 `strategy.id`가 영구 식별자다.

## 키워드

| 위치 | 키워드 | 의미 | 필수 |
|---|---|---|---|
| 최상위 | `strategy` | 전략 식별 정보 | 예 |
| `strategy` | `id` | 전략의 고유 ID | 예 |
| `strategy` | `name` | 화면에 표시할 전략 이름 | 예 |
| `strategy` | `version` | 전략 규칙의 버전 | 예 |
| `strategy` | `dsl_version` | 사용하는 DSL 문법 버전, 기본값 `1` | 아니요 |
| `strategy` | `enabled` | 자동 로딩 여부, 기본값 `true` | 아니요 |
| 최상위 | `assets` | 전략에서 사용하는 종목 정보 | 예 |
| `assets` | `required` | 가격과 지표가 필요한 종목 목록 | 예 |
| `assets` | `risk` | 위험자산으로 표시할 종목 또는 종목 목록 | 아니요 |
| 최상위 | `parameters` | 실행 중 변하지 않는 사용자 정의 값 | 아니요 |
| 최상위 | `variables` | 평가일마다 계산하는 사용자 정의 값 | 아니요 |
| 최상위 | `state` | 다음 평가일까지 유지하는 사용자 정의 값 | 아니요 |
| `state.<이름>` | `initial` | 상태의 초기값 | 예 |
| `state.<이름>` | `check` | 상태 평가 주기, 기본값 `daily` | 아니요 |
| `state.<이름>` | `rules` | 상태 변경 규칙 목록 | 아니요 |
| `state.rules[]` | `when` | 규칙을 적용할 조건 | 조건부 |
| `state.rules[]` | `otherwise` | 앞선 조건이 적용되지 않았을 때 사용할 규칙 | 조건부 |
| `state.rules[]` | `set` | 상태에 저장할 값 | 예 |
| `state.rules[]` | `confirm` | 상태 변경 전 연속 확인일수, 기본값 `1` | 아니요 |
| `state` | `market_mode` | 그래프와 성과 분석에 전달할 예약 시장 상태 | 아니요 |
| 최상위 | `target` | 목표 비중 규칙 목록 | 예 |
| `target[]` | `when` | 해당 목표 비중을 사용할 조건 | 아니요 |
| `target[]` | `weights` | 종목별 목표 비중 | 예 |
| 최상위 | `rebalance` | 순서대로 검사할 리밸런싱 규칙 목록 | 아니요 |
| `rebalance[]` | `when` | 리밸런싱할 조건 | 예 |
| `rebalance[]` | `check` | 조건 평가 주기, 기본값 `daily` | 아니요 |
| `rebalance[]` | `days` | 이 규칙이 실행될 때의 분할 실행일수 | 아니요 |
| 최상위 | `execution` | 리밸런싱 실행 설정 | 아니요 |
| `execution` | `days` | 분할 실행일수, 기본값 `1` | 아니요 |
| 최상위 | `source` | 실제 상품 매핑에 사용할 기존 전략 ID | 상품 매핑 시 예 |
| 최상위 | `products` | 기준 종목과 실제 상품의 매핑 | 상품 매핑 시 예 |

## 자산과 티커

`assets.required`와 목표 비중의 키에는 전략이 사용하는 자산 식별자를 넣습니다. 일반적인
자산은 Yahoo Finance에서 사용하는 티커 심볼(ticker symbol)을 사용합니다. 예를 들어
`QQQ`는 Invesco QQQ ETF, `BND`는 Vanguard Total Bond Market ETF, `SPY`는 SPDR S&P
500 ETF의 티커입니다. 한국 종목은 `426030.KS`처럼 시장 접미사를 포함할 수 있습니다.

`TDF2050_PROXY`도 `QQQ`, `BND`처럼 전략에서 사용할 수 있는 자산 식별자입니다. 다만
Yahoo Finance 티커가 아닌 합성 자산입니다. 필요한 경우 `SPY` 40.81%, `VXUS` 33.39%,
`BND` 25.80%를 월별 리밸런싱한 가격 흐름으로 동적으로 계산합니다. 실제 TDF 상품 가격으로
대체하지 않으며, 실제 TDF의 액티브 운용·국내채권·글라이드패스를 그대로 재현하지는 않습니다.

상태 변수의 이름과 값은 전략 작성자가 정의한다.

### 시장 상태

`market_mode`는 선택적으로 사용할 수 있는 예약 상태 변수다. 이 변수가 정의되어
있으면 `state` 안의 작성 순서와 관계없이 엔진이 전략의 대표 시장 상태로 사용한다.
백테스트의 `StrategyState`, 그래프의 상태 배경색, 상태 전환 사유와 성과 기여도
분석에도 자동으로 전달된다.

```yaml
state:
  market_mode:
    initial: UNINITIALIZED
    rules:
      - {when: variables.structural_bear, set: BEAR}
      - {when: variables.risk_off_score >= 5, set: CAUTION}
      - {otherwise: true, set: BULL}
```

허용값은 `BULL`, `CAUTION`, `BEAR`, `RECOVERY`다. `UNINITIALIZED`는 최초 평가에서
시장 상태를 선택하기 전까지만 초기값으로 사용할 수 있다. 첫 평가가 끝난 뒤에도
`UNINITIALIZED`이거나 다른 값이면 엔진이 오류로 알려준다. 이 상태들은 시장 구간을
표시할 뿐이며 목표 비중과 리밸런싱 규칙은 전략 작성자가 별도로 정의한다.

`market_mode`를 정의하지 않는 전략은 이 네 상태를 사용할 필요가 없다. 다른 상태
변수의 이름과 값은 계속 자유롭게 정할 수 있다.

## 주석

`#` 뒤의 내용은 주석으로 처리되며 전략 실행에는 영향을 주지 않는다. 한 줄 전체를
주석으로 사용하거나 값 뒤에 설명을 작성할 수 있다.

```yaml
# 위험자산과 안전자산의 기본 목표 비중
target:
  - weights:
      QQQ: 70%  # 위험자산
      BND: 30%  # 안전자산
```

## 표현식

표현식에서 지원하는 연산은 다음과 같다.

- 산술: `+`, `-`, `*`, `/`, `%`, `**`
- 비교: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not in`
- 논리: `and`, `or`, `not`
- 함수: `abs`, `min`, `max`, `sum`, `count`, `all`, `any`, `round`, `clamp`
- 상태: `changed(state.name)`, `previous(state.name)`
- 현재 비중: `portfolio.weight.QQQ`
- 비중 차이: `target_deviation()` — 현재 비중과 목표 비중의 최대 차이
- 퍼센트 리터럴: `70%`, `-8%`

시장 데이터는 `QQQ.close`, `QQQ.ema200`, `BND.roc40`처럼 참조한다. 대소문자는
구분하지 않는다. 설정과 계산값은 `parameters.maximum_risk`,
`variables.risk_score`, 상태는 `state.market_mode`로 참조한다.

엔진은 YAML 표현식에 사용된 종목과 지표를 자동으로 찾는다. 필요한 지표 열이
없으면 실행 전에 계산하고, 모든 필수 지표 값이 준비된 날짜부터 전략을 평가한다.
따라서 전략 YAML에서 `indicators_ready` 같은 데이터 준비 확인 변수를 별도로
작성할 필요가 없다. 엔진에서 계산할 수 없는 지표를 참조하면 실행 전에 오류로
알려준다.

## 계산 변수

```yaml
parameters:
  warning_score: 5

variables:
  risk_off_score: >
    count(
      QQQ.close < QQQ.ema20,
      QQQ.close < QQQ.ema55,
      QQQ.ema20 < QQQ.ema55,
      QQQ.roc20 < 0
    )

  structural_bear: >
    variables.risk_off_score >= parameters.warning_score
    and QQQ.close < QQQ.ema200
    and QQQ.drawdown120 <= -8%
```

변수는 작성 순서대로 계산되므로 뒤의 변수가 앞의 변수를 참조할 수 있다.

## 사용자 정의 상태

```yaml
state:
  risk_level:
    initial: 70%
    check: monthly
    rules:
      - {when: QQQ.drawdown120 <= -20%, set: 20%}
      - {when: QQQ.drawdown120 <= -10%, set: 40%}
      - {otherwise: true, set: 70%}
```

규칙은 위에서부터 평가하며 처음 일치한 규칙만 적용한다. 일치 규칙이 없으면
기존 값을 유지한다. `normal` 같은 문자열 `set` 값은 그대로 저장하고 `70%` 같은
퍼센트 값은 계산 가능한 숫자로 저장한다. 계산 결과를 저장하려면 앞에 `=`를 붙인다.

```yaml
state:
  dynamic_weight:
    initial: 50%
    rules:
      - {when: variables.volatility_ok, set: "=variables.suggested_weight"}
```

`check`는 상태 규칙을 평가할 주기다. `daily`, `weekly`, `monthly`, `quarterly`를
지원하며 생략하면 매 거래일 평가한다. `confirm`은 설정한 평가 주기에서 조건이
연속으로 만족된 횟수를 뜻한다.

```yaml
state:
  trend:
    initial: normal
    rules:
      - {when: QQQ.close < QQQ.ema200, set: defensive, confirm: 3}
      - {when: QQQ.close > QQQ.ema200, set: normal, confirm: 3}
```

`changed(state.trend)`는 확인 기간을 통과하여 실제 값이 변경된 평가일에만 참이다.
`previous(state.trend)`는 그 평가일 시작 시점의 값이다.

## 목표 비중

고정 목표:

```yaml
target:
  - weights:
      QQQ: 70%
      BND: 30%
```

상태별 목표:

```yaml
target:
  - when: state.trend == 'defensive'
    weights: {QQQ: 20%, BND: 80%}

  # when이 없는 마지막 항목은 기본 목표다.
  - weights: {QQQ: 70%, BND: 30%}
```

계산 목표:

```yaml
target:
  - weights:
      QQQ: state.risk_level
      BND: 1 - state.risk_level
```

`target`은 항상 목록이며 각 항목의 비중은 `weights` 아래에 작성한다. 단일 고정
목표도 같은 형식을 사용한다. 조건이 있는 항목은 위에 두고, `when`이 없는 기본
목표는 마지막에 둔다. 나머지 비중은 `1 - state.risk_level`처럼 계산할 수 있다.
최종 비중은 음수가 아니어야 하고 합계가 정확히 100%여야 한다.

## 리밸런싱과 실행

```yaml
rebalance:
  - check: monthly
    when: target_deviation() >= 5%

execution:
  days: 3
```

규칙은 위에서부터 검사하고 처음 일치한 규칙만 실행한다. `when`은 리밸런싱할
조건이고 `check`는 그 조건을 평가할 주기다. `check`를 생략하면
매 거래일 평가한다. `target_deviation()`은 현재 비중과 이번에 계산한 목표 비중의
차이 중 가장 큰 값을 반환한다. 따라서 위 예시는 매월 한 번 비중 차이가 5%p 이상인지
확인한다. 상태가 바뀔 때 즉시 리밸런싱하려면 다음처럼 작성한다.

```yaml
rebalance:
  - when: changed(state.risk_level)
    days: 3
  - check: monthly
    when: target_deviation() >= 5%
    days: 1
```

각 규칙의 `days`를 생략하면 `execution.days`를 사용한다. 검사 주기는 `daily`,
`weekly`, `monthly`, `quarterly`를 지원한다. 최초 평가는
백테스트 엔진이 초기 투자로 처리하므로 DSL의 리밸런싱 신호는 거짓으로 반환한다.

## 실제 상품 매핑

`source`에 기존 전략 ID를 지정하고 `products`에서 기준 종목을 실제 상품으로
매핑한다. 기준 전략이 계산한 종목별 목표 비중은 설정한 비율에 따라 실제 상품으로
분배된다.

```yaml
strategy:
  id: pension-nasdaq-mix
  name: Pension Nasdaq Mix
  version: 1

source: retirement-allocation-profit-band-vxus

products:
  QQQ:
    379810.KS: 50%
    426030.KS: 30%
    0015B0.KS: 20%

  BND:
    437080.KS: 100%

  BIL:
    0046A0.KS: 100%
```

각 기준 종목 아래의 실제 상품 비율 합계는 100%여야 한다. 매핑하지 않은 종목은
기존 종목을 그대로 사용한다. 현재 상품 비중은 같은 기준 종목에 속한 상품들을
합산하여 원본 전략의 리밸런싱 조건에 전달한다.
한국 거래소 상품의 환율 정보는 상품 코드의 접미사를 기준으로 파이썬 엔진이
자동으로 판단하므로 YAML에 별도 환율 항목을 작성하지 않는다.
