# 선언형 전략 DSL v1

이 DSL은 전략 파일 하나만 읽어도 전체 전략 설정을 알 수 있도록 설계한다.
전략끼리 `extends`, `parent`, `include`로 상속하지 않는다. 새 전략은 기존
YAML을 복사한 뒤 ID와 규칙을 변경한다.

## 가장 작은 전략

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
  QQQ: 70%
  BND: 30%

rebalance:
  check: daily
  drift: 5%

execution:
  days: 1
```

`strategies` 디렉터리의 `enabled: true`인 YAML은 `build_runner()`가 자동으로
읽는다. 파일명은 실행 의미에 영향을 주지 않으며 `strategy.id`가 영구 식별자다.

## 최상위 키워드

| 키 | 의미 | 필수 |
|---|---|---|
| `strategy` | ID, 표시 이름, 전략 버전, DSL 버전, 활성 여부 | 예 |
| `assets` | 필요한 데이터 종목과 위험자산 표시 그룹 | 예 |
| `parameters` | 실행 중 변하지 않는 사용자 설정 | 아니요 |
| `variables` | 평가일마다 다시 계산하는 파생값 | 아니요 |
| `state` | 다음 평가일까지 기억하는 사용자 정의 값 | 아니요 |
| `target` | 목표 비중 또는 조건별 목표 비중 | 예 |
| `rebalance` | 조건, 검사 주기, 허용 편차 | 아니요 |
| `execution` | 리밸런싱 분할 실행일 | 아니요 |

`BULL`, `BEAR`, `risk_level` 같은 이름과 값은 예약어가 아니다. 전략 작성자가
필요한 이름을 정의한다.

## 표현식

표현식은 Python 코드가 아니라 허용 목록 기반의 작은 문법으로 해석한다.
파일 접근, import, 속성 메서드 호출과 임의 Python 실행은 허용하지 않는다.

지원 연산은 다음과 같다.

- 산술: `+`, `-`, `*`, `/`, `%`, `**`
- 비교: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not in`
- 논리: `and`, `or`, `not`
- 함수: `abs`, `min`, `max`, `sum`, `count`, `all`, `any`, `round`, `clamp`
- 상태: `changed(state.name)`, `previous(state.name)`
- 퍼센트 리터럴: `70%`, `-8%`

시장 데이터는 `QQQ.close`, `QQQ.ema200`, `BND.roc40`처럼 참조한다. 대소문자는
구분하지 않는다. 설정과 계산값은 `parameters.maximum_risk`,
`variables.risk_score`, 상태는 `state.market_mode`로 참조한다.

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
    rules:
      - {when: QQQ.drawdown120 <= -20%, set: 20%}
      - {when: QQQ.drawdown120 <= -10%, set: 40%}
      - {otherwise: true, set: 70%}
```

규칙은 위에서부터 평가하며 처음 일치한 규칙만 적용한다. 일치 규칙이 없으면
기존 값을 유지한다. 문자열 `set` 값은 그대로 저장한다. 계산 결과를 저장하려면
앞에 `=`를 붙인다.

```yaml
state:
  dynamic_weight:
    initial: 50%
    rules:
      - {when: variables.volatility_ok, set: "=variables.suggested_weight"}
```

`confirm`은 조건이 연속으로 만족된 뒤 상태를 바꾼다.

```yaml
state:
  trend:
    initial: normal
    rules:
      - {when: QQQ.close < QQQ.ema200, set: defensive, confirm: 3}
      - {when: QQQ.close > QQQ.ema200, set: normal, confirm: 3}
```

`changed(state.trend)`는 확인 기간을 통과하여 실제 값이 변경된 평가일에만 참이다.
`previous(state.trend)`는 그 평가일 시작 시점의 값이다. `trend_changed` 같은 숨은
변수는 만들지 않는다.

## 목표 비중

고정 목표:

```yaml
target:
  QQQ: 70%
  BND: 30%
```

상태별 목표:

```yaml
target:
  - when: state.trend == 'defensive'
    weights: {QQQ: 20%, BND: 80%}

  - otherwise: true
    weights: {QQQ: 70%, BND: 30%}
```

계산 목표:

```yaml
target:
  QQQ: state.risk_level
  BND: remaining
```

`remaining`은 다른 비중을 뺀 나머지를 뜻하며 한 목표에서 한 종목에만 사용할 수
있다. `keep_current`는 현재 실제 비중을 목표로 유지한다. 최종 비중은 음수가
아니어야 하고 합계가 정확히 100%여야 한다.

## 리밸런싱과 실행

```yaml
rebalance:
  when: changed(state.risk_level)
  check: monthly
  drift: 5%

execution:
  days: 3
```

`when`과 드리프트 검사는 OR 관계다. 검사 주기는 `daily`, `weekly`, `monthly`,
`quarterly`를 지원한다. 최초 평가는 백테스트 엔진이 초기 투자로 처리하므로 DSL의
리밸런싱 신호는 거짓으로 반환한다.

## 확장 연산자

새로운 변수나 상태 이름은 엔진 수정 없이 추가한다. 새로운 계산 알고리즘만 Python
확장 연산자로 등록한다.

```python
registry.register("weighted_momentum", weighted_momentum, version="1")
```

등록한 함수는 표현식에서 `weighted_momentum(...)`으로 호출할 수 있다. 이름과
버전을 고정하고 별도 단위 테스트를 작성해야 한다. 전략 하나에서만 쓰이며 상태와
외부 자원을 많이 소유하는 알고리즘은 DSL을 확장하지 말고 Python 전략으로 유지한다.

## 기존 전략에서 도출한 기능 분류

현재 운영·실험·검증 코드의 26개 `evaluate()` 구현과 이를 상속한 전략을 다음처럼
분류했다.

| 전략군 | 발견된 동작 | 처리 방향 |
|---|---|---|
| 고정/밴드 (`RETIREMENT_7030_BAND`, static 계열) | 고정 목표, 실제 비중 편차, 주기 | DSL v1 핵심 |
| 퇴직연금 상태 전략 | 조건 점수, 사용자 상태, 연속 확인, 상태별 비중, 실행일 | DSL v1 핵심 |
| 선택적 상태 전환 | 상태는 변경하지만 주문은 보류, 안전자산 변경 예외 | 목표/주문 분리 확장 |
| Safe Blend | 모멘텀 스프레드 구간별 BND/BIL 배분 | 비중 변환 연산자 |
| VXUS/SPY/USMV 대체 | 자산군 한도 내 원천 비중 대체 | 비중 변환 연산자 |
| Profit Band | 기본 목표와 실행 목표 분리, 현재 이익 비중 보존 | 비중 변환 연산자 |
| 연금상품 매핑 | 지수 자산 비중을 여러 실제 상품으로 배분 | 상품 매핑 연산자 |
| 비대칭/추적 전략 | 고점·저점, trailing target, 래치와 비대칭 밴드 | 시계열 상태 연산자 |
| Downside/연속 배분 | 변동성 목표, 연속 위험 비중, clamp | 수학 핵심 + 확장 연산자 |
| 모멘텀 랭킹/다중시장 | 여러 종목 정렬, 상위 N개 선택, 상대 모멘텀 | 횡단면 확장 연산자 |
| 확률/위험 예측 | 회귀, expanding/rolling 학습, 예측 버퍼 | Python 확장 또는 Python 전략 |
| Tail risk/정밀 경고 | 다단계 점수, 경로 의존 경고, 지속 조건 | 상태 핵심 + 전용 연산자 |

핵심 문법은 전략명이나 특정 시장 상태를 알지 않는다. 반복되는 계산만 확장 연산자로
승격하며, 모델 학습처럼 YAML로 표현했을 때 더 어려워지는 로직은 Python에 남긴다.

## 전환 검증

기존 전략을 YAML로 전환할 때는 같은 날짜의 다음 값을 비교한다.

1. 계산 변수와 상태
2. 기본 목표 및 최종 목표 비중
3. 리밸런싱 여부와 사유
4. 분할 실행일과 거래 내역
5. 전체 백테스트 성과

Python 구현은 동등성 테스트가 통과할 때까지 기준 구현으로 유지한다. YAML 파일은
다른 전략을 참조하지 않으며, 수정 시 `strategy.version`을 올려 결과 캐시가 섞이지
않게 한다.
