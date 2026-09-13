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
| `assets` | `observations` | 신호에는 쓰지만 목표 비중에는 넣지 않는 관찰 종목 목록 | 아니요 |
| `assets` | `risk` | 위험자산 종목 목록. 로테이션 전략은 목표 비중에서 이 합계를 최대 70%로 제한 | 아니요 |
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
| 최상위 | `notifications` | 알림 표시·즉시 상태·확인·사전주의 규칙 | 아니요 |
| `notifications` | `weekly` | 주간 시장 브리핑 생성 여부, 기본값 `true` | 아니요 |
| `notifications` | `states` | 메시지에 표시하고 상태 전환을 감시할 상태 설정 | 아니요 |
| `notifications.states.<이름>` | `label` | 상태의 표시 이름 | 예 |
| `notifications.states.<이름>` | `alerts` | 상태 변경 또는 확인 시작을 알릴 규칙. 생략하면 모든 상태 변경을 알림 | 아니요 |
| `notifications.states.<이름>.alerts[]` | `on` | 알림 시점. `changed` 또는 `confirmation_started`, 기본값 `changed` | 아니요 |
| `notifications.states.<이름>.alerts[]` | `from` | 전환 전 상태. 생략하면 모든 출발 상태에 적용 | 아니요 |
| `notifications.states.<이름>.alerts[]` | `to` | 목표 상태 또는 목표 상태 목록 | 예 |
| `notifications.states.<이름>.alerts[]` | `message` | 전환 정보 뒤에 붙일 상세 설명 | 아니요 |
| `notifications` | `variables` | 메시지에 표시할 계산 변수 설정 | 아니요 |
| `notifications.variables.<이름>` | `label` | 계산 변수의 표시 이름 | 예 |
| `notifications.variables.<이름>` | `max` | 점수형 변수의 최대값 | 아니요 |
| `notifications.variables.<이름>` | `decimals` | 표시할 소수점 자릿수, 기본값 `0` | 아니요 |
| `notifications` | `market` | 메시지에 표시할 시장 지표 목록 | 아니요 |
| `notifications.market[]` | `ticker` | 지표를 읽을 설정 자산 | 예 |
| `notifications.market[]` | `field` | 표시할 시장 데이터 필드 | 예 |
| `notifications.market[]` | `label` | 시장 지표의 표시 이름 | 예 |
| `notifications.market[]` | `format` | `number`, `price`, `percent`, `ratio_percent` 중 표시 형식 | 아니요 |
| `notifications.market[]` | `decimals` | 표시할 소수점 자릿수, 기본값 `1` | 아니요 |
| `notifications` | `prealerts` | 상태 전환과 독립적인 사전주의 규칙 목록 | 아니요 |
| `notifications.prealerts[]` | `id` | 중복 억제 상태를 구분하는 고유 ID | 예 |
| `notifications.prealerts[]` | `when` | 사전주의를 발생시킬 DSL 조건 | 예 |
| `notifications.prealerts[]` | `reset_when` | 같은 사전주의를 다시 허용할 DSL 조건 | 예 |
| `notifications.prealerts[]` | `message` | 사전주의에 표시할 설명 | 예 |
| 최상위 | `rotation` | 현금 슬리브만 자동 대체하는 교차자산 선택 규칙 | 아니요 |
| `rotation` | `replace` | 자동 대체할 안전자산 티커 | 예 |
| `rotation` | `assets` | 비교할 후보 자산 티커 목록 | 예 |
| `rotation` | `review` | 후보 재평가 주기, 기본값 `monthly` | 아니요 |
| `rotation` | `suspend_when` | 참인 동안 후보 교체를 중단하고 `replace` 자산을 유지하는 조건 | 아니요 |
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

통화 평가는 YAML에 작성하지 않습니다. 해외 자산을 자동 교체 후보로 함께 쓰는 전략에서는
엔진이 추세·모멘텀 신호에는 현지 통화 가격을 사용하고, 보유 비중·목표 편차·성과는
`KRW=X`로 원화 환산합니다. 국내 `.KS`·`.KQ` 종목은 원화 가격을 그대로 사용하며,
종목별 `_KRW` CSV를 만들 필요가 없습니다.

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
- 비중 차이: `target_deviation()` — 현재 비중과 목표 비중의 최대 절대 차이
- 자산별 방향 괴리: `weight_deviation('QQQ')` — 해당 자산의 현재 비중에서 목표
  비중을 뺀 값. 음수는 과소비중, 양수는 과대비중이다.
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

## 자동 교차자산 로테이션

`rotation`은 기본 목표에서 지정한 현금/안전자산 슬리브만 후보 자산으로 자동 대체한다.
사람이 종목을 고르거나 매월 승인하지 않는다. 후보 자산은 `target`에 적지 않는다. 엔진이
선택되지 않은 후보의 기본 비중을 0%로 처리하고 슬리브 비중 안에서만 대체 비중을 계산하므로
QQQ·TDF 등 나머지 목표 비중은 바뀌지 않는다. 최종 주문 목표에는 이전에 선택한 후보를
청산할 수 있도록 선택되지 않은 후보도 내부적으로 0%가 적용된다.

```yaml
target:
  - weights:
      QQQ: 20%
      TDF2050_PROXY: 20%
      BIL: 60%
```

```yaml
rotation:
  replace: BIL
  review: monthly
  suspend_when: variables.structural_bear
  assets: [GLD, IEF, 069500.KS]
```

`suspend_when`이 참이면 현재 후보 선택을 해제하고 `target`에 선언된 `replace`
자산 비중을 그대로 유지한다. 조건이 해제되면 다음 `review` 주기에 후보를 다시 선택한다.

`replace` 자산의 목표 비중이 0%보다 큰 달에만 다음 순서로 평가한다. BIL처럼 기본
안전자산 비중이 없는 BULL 구간에서는 이 과정이 실행되지 않는다.

1. 후보별 기본 조건을 확인한다. 가격이 EMA200 위에 있고, 3개월 수익률이 `replace`
   자산보다 높으며, 3·6·12개월 수익률과 최근 60일 가격변동성 데이터가 모두 있어야 한다.
   조건을 하나라도 충족하지 못하면 그 달에는 사지 않는다.
2. 적격 후보의 점수를 계산한다. 점수는 `0.5 × 3개월 초과수익률 + 0.3 × 6개월
   초과수익률 + 0.2 × 12개월 초과수익률`이다. 여기서 초과수익률은 각 기간의 후보
   수익률에서 `replace` 자산 수익률을 뺀 값이다.
3. 점수가 높은 최대 두 자산을 고른다. 기존에 보유한 후보가 계속 적격이면 최소 3번의
   월간 검토 기간 동안 우선 유지한다. 새 후보가 기존 후보보다 점수 3 이상 높을 때만
   교체하므로, 작은 순위 변화만으로 매달 종목을 바꾸지 않는다.
4. 선택된 자산들의 초기 비율을 정한다. 최근 가격변동성이 낮은 자산에는 더 많이,
   가격변동성이 높은 자산에는 더 적게 배분한다. 예를 들어 한 자산의 최근 가격변동성이
   5%이고 다른 자산이 10%이면, 먼저 약 2 대 1 비율로 나눈다.
5. 아래의 자산별 상한을 적용한다. 상한은 모두 `replace` 슬리브 안에서의 비율이다.

   - 채권 후보: 후보 하나당 슬리브의 최대 50%. 채권 두 개가 선택되면 각각 최대 50%씩
     배분되어 슬리브 전체를 대체할 수 있다.
   - 금 후보: `GLD`는 슬리브의 최대 30%.
   - 주식성 후보: `069500.KS`, `VEA`, `VWO`가 선택된 경우, 이들 합계는 슬리브의 최대
     30%. 주식성 후보가 하나면 그 한 종목의 최대도 30%이고, 두 개면 둘이 그 30%를 나눈다.

6. 상한을 적용하고 남은 비중은 `replace` 자산에 그대로 둔다. 예를 들어 BIL 슬리브가
   80%이고 금과 주식성 후보가 선택되면, 각각 최대 전체 24%(= 80% × 30%)까지만
   배분되고 최소 32%는 BIL에 남는다. 선택된 후보 중 `assets.risk`에 등록된 위험자산이
   있어 전체 목표 위험자산 합계가 70%를 넘으면, 그 초과분도 `replace` 자산에 남긴다.

계산된 후보별 비중이 이전 구성과 비교해 슬리브 기준 10%p 이하로만 달라지면 기존 구성을
유지한다. 슬리브 비중이 잠시 0%가 되면 선택을 삭제하지 않고 휴면 상태로 보존한다.

검토 주기에 선택 또는 비중이 달라지면 자동으로 리밸런싱한다. 일별 기록의
`RotationDecision`에는 적격/탈락 사유, 각 후보 점수, 이전·새 선택, 최종 비중 및 사람이
읽을 수 있는 설명이 남는다. 따라서 자동 집행이지만 결과를 사후 검증할 수 있다.

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

## 알림 규칙

`notifications`는 선택 항목이며 목표 비중이나 리밸런싱 판단을 바꾸지 않는다. 기존
전략에 이 항목이 없어도 DSL 호환성은 유지된다. 이때 엔진은 정의된 상태와 변수, 첫 번째
위험자산(없으면 첫 번째 필수 자산), 단순한 `target_deviation()` 조건을 이용해 기본 알림을
구성한다. 명시적으로 설정하면 전략 작성자가 알림 내용과 발생 시점을 정할 수 있다.

```yaml
notifications:
  weekly: true
  states:
    risk_regime:
      label: 위험 국면
      alerts:
        - on: confirmation_started
          to: [DEFENSIVE, RECOVERY]
        - on: changed
          from: NORMAL
          to: DEFENSIVE
          message: 방어 조건이 확인되었습니다
        - on: changed
          from: DEFENSIVE
          to: RECOVERY
          message: 회복 조건이 확인되었습니다
  variables:
    signal_count: {label: 충족 신호, max: 4}
  market:
    - {ticker: VTI, field: roc1, label: 1일, format: percent}
    - {ticker: VTI, field: drawdown120, label: 120일 고점 대비, format: ratio_percent}
  prealerts:
    - id: allocation-drift
      when: target_deviation() >= 3%
      reset_when: target_deviation() < 2%
      message: 목표 비중과 현재 비중의 차이가 커졌습니다
```

- `weekly`: 주간 시장 브리핑 생성 여부다. 기본값은 `true`다.
- `states.<name>.label`: 메시지에 표시할 상태명이다. `alerts`를 생략하면 해당 상태의
  모든 변화를 `위험 국면: NORMAL → DEFENSIVE`처럼 알린다. `alerts[].from`과
  `alerts[].to`를 작성하면 나열한 전환만 알리고, 선택 항목인 `message`로 이유를 덧붙인다.
  실제 메시지에는
  `위험 국면: NORMAL → DEFENSIVE · 방어 조건이 확인되었습니다`처럼 전환과 설명을 함께
  표시한다. `from`을 생략하면 출발 상태와 관계없이 `to`로 바뀌는 모든 전환에 적용한다.
  구체적인 `from` 규칙과 생략한 규칙이 함께 일치하면 구체적인 규칙을 우선한다.

```yaml
# 모든 risk_regime 전환을 기본 형식으로 알림
states:
  risk_regime:
    label: 위험 국면

# 지정한 전환만 알림. message가 없으면 전환 정보만 표시
states:
  risk_regime:
    label: 위험 국면
    alerts:
      - on: changed
        from: NORMAL
        to: DEFENSIVE
      - on: changed
        from: DEFENSIVE
        to: RECOVERY
        message: 회복 조건이 확인되었습니다
```
- `variables`: 메시지에 표시할 계산 변수다. `max`를 쓰면 `충족 신호 3/4`처럼 표시한다.
- `market`: 메시지에 표시할 종목 지표다. 계산할 수 없는 초기 구간의 값은 생략한다.
- `alerts[].on`: 생략하거나 `changed`이면 상태가 실제로 변경될 때 알린다.
  `confirmation_started`이면 `confirm`이 설정된 목표 상태를 향한 연속 확인이 시작되는
  첫날에 알린다. 이때 `to`에는 하나의 상태값 또는 `[BEAR, RECOVERY]` 같은 상태값 목록을
  사용할 수 있다.
- `prealerts`: 기존 DSL 표현식으로 독립적인 사전주의 조건을 만든다. `id`별로 한 번만
  보내고 `reset_when`이 참이 된 뒤에만 다시 보낸다. 임계값의 의미는 전략마다 다르므로
  알림 DSL에 공통 실행 임계값을 따로 두지 않는다.

실제 리밸런싱 여부와 실행일수는 각각 `rebalance[].when`과 `execution.days`가 유일하게
결정한다. 예를 들어 비중 괴리를 쓰지 않고 상태 전환이나 달력만으로 리밸런싱하는 전략도
동일한 알림 구조를 사용할 수 있다.

실제 상품 매핑 전략은 별도 알림 설정을 갖지 않고 기준 전략의 `notifications`를 상속한다.
상태와 시장 신호는 기준 전략 기준으로 설명하되 현재·목표·변경 비중은 실제 상품으로 표시한다.

### 알림 값 표시 형식

`market[]`의 `format`은 원본 지표 값을 메시지에 어떻게 표시할지 지정한다.

| `format` | 원본 값의 단위 | 원본 값 예시 | 메시지 표시 | 용도 |
|---|---:|---:|---:|---|
| `number` | 일반 숫자 | `72.345` | `72.3` | 점수, 배수, 일반 지표 |
| `price` | 가격 숫자 | `245.678` | `245.7` | 종가, 이동평균 등 가격 지표 |
| `percent` | 이미 퍼센트 단위인 숫자 | `-2.35` | `-2.4%` | `roc1`, `roc5`, `roc20` 등 |
| `ratio_percent` | 1을 100%로 보는 비율 | `-0.0835` | `-8.4%` | `drawdown120`, 비율형 변동성 등 |

`percent`는 원본 값에 100을 곱하지 않는다. 반면 `ratio_percent`는 원본 값에 100을
곱해서 표시한다. 따라서 수익률 지표가 `-2.35`처럼 퍼센트 단위로 저장되어 있다면
`percent`를 사용하고, 낙폭이 `-0.0835`처럼 비율로 저장되어 있다면
`ratio_percent`를 사용한다.

`decimals`로 표시할 소수점 자릿수를 `0`부터 `6`까지 지정할 수 있으며 기본값은 `1`이다.

```yaml
market:
  - {ticker: VTI, field: close, label: 종가, format: price, decimals: 2}
  - {ticker: VTI, field: roc5, label: 5일 수익률, format: percent, decimals: 1}
  - {ticker: VTI, field: drawdown120, label: 120일 낙폭, format: ratio_percent, decimals: 1}
```

위 설정에서 원본 값이 각각 `245.678`, `-2.35`, `-0.0835`라면 알림에는
`종가 245.68 / 5일 수익률 -2.4% / 120일 낙폭 -8.4%`로 표시된다.
