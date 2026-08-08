# RetirementAllocationStrategy와 상품 매핑

[← 전체 전략](../strategy.md)

## RetirementAllocationStrategy

### 목적과 자산

QQQ를 위험자산 신호 및 기본 매매 자산으로 사용하고, BND와 BIL을 안전자산으로
사용하는 퇴직연금 동적 자산배분 전략입니다.

필요 ticker: `QQQ`, `BND`, `BIL`

### 상태별 자산배분

매월 선택한 안전자산 한 종목이 표의 안전자산 비중 전체를 받습니다.

| 상태 | QQQ | 선택된 BND 또는 BIL | 실행 일수 |
|---|---:|---:|---:|
| `BULL` | 70% | 30% | 5일 |
| `CAUTION` | 70% | 30% | 2일 |
| `BEAR` | 0% | 100% | 1일 |
| `RECOVERY` | 50% | 50% | 3일 |

모든 상태에서 인정되는 안전자산이 최소 30%이며 위험자산은 최대 70%입니다.

### 단기 점수

QQQ의 다음 여섯 조건을 매일 계산합니다.

`risk_off_score`는 참인 하락 조건의 개수입니다.

1. 종가 < EMA20
2. 종가 < EMA55
3. EMA20 < EMA55
4. ROC5 < 0
5. ROC20 < 0
6. EMA20의 5일 기울기 < 0

`recovery_score`는 위 부등호를 반대로 적용한 상승 조건의 개수입니다.

### 구조적 하락 조건

`BEAR` 후보가 되려면 다음 조건을 모두 만족해야 합니다.

- `risk_off_score >= 5`
- `종가 < EMA20 < EMA55 < EMA200`
- ROC60 < 0
- EMA200의 20일 기울기 < 0
- 최근 120일 고점 대비 낙폭 ≤ -8%

단기 약세 점수만으로 `BEAR`에 진입하지 않고 장기 추세와 낙폭을 함께 확인합니다.

### 상태 전이

```text
초기
├── 구조적 하락 충족 → BEAR
├── risk_off_score >= 5 → CAUTION
└── 그 외 → BULL

BULL
├── 구조적 하락 10일 확인 → BEAR
├── risk_off_score >= 5를 3일 확인 → CAUTION
└── 그 외 → BULL 유지

CAUTION
├── 구조적 하락 10일 확인 → BEAR
├── recovery_score >= 4를 3일 확인 → BULL
└── 그 외 → CAUTION 유지

BEAR
├── recovery_score >= 3을 2일 확인 → RECOVERY
└── 그 외 → BEAR 유지

RECOVERY
├── 구조적 하락 10일 확인 → BEAR
├── recovery_score >= 4
│   + 종가 > EMA55
│   + ROC60 > 0을 3일 확인 → BULL
├── risk_off_score >= 5를 3일 확인 → CAUTION
└── 그 외 → RECOVERY 유지
```

후보 상태가 중간에 달라지거나 현재 상태와 같아지면 연속 확인 일수는 초기화됩니다.

### 안전자산 선택

월이 바뀔 때 BND와 BIL의 ROC40을 비교합니다.

- 최초 선택은 ROC40이 더 높은 자산입니다.
- 현재 BND라면 BIL의 ROC40이 BND보다 0.25%p 이상 높을 때 BIL로 전환합니다.
- 현재 BIL이라면 BND의 ROC40이 BIL보다 0.25%p 이상 높을 때 BND로 전환합니다.
- 0.25%p 버퍼 안에서는 기존 선택을 유지합니다.

안전자산이 변경되면 상태가 같더라도 새로운 목표로 리밸런싱합니다.

### 일반 리밸런싱

상태 전환이 없을 때는 월 1회 실제 비중을 확인합니다. 어느 종목이든 목표에서
5%p 이상 벗어나면 현재 상태의 목표 비중으로 리밸런싱합니다.

## RetirementAllocationSelectiveRebalanceStrategy

`RetirementAllocationStrategy`를 직접 상속하며 상태 판단, 확인 기간, 상태별 목표
비중, 안전자산 선택과 실행 일수는 부모와 같습니다. 차이는 `CAUTION→BULL` 전환
시 실제 주문을 만들지 여부뿐입니다.

다음 조건을 모두 만족하면 상태는 `BULL`로 갱신하되 주문은 생략합니다.

1. 전환 전후 목표 비중이 동일합니다.
2. BND와 BIL의 안전자산 교체가 없습니다.
3. 실제 자산별 비중이 목표의 5%p 밴드 안에 있습니다.

안전자산이 변경됐거나 5%p 밴드를 벗어났다면 부모 전략과 동일하게 주문합니다.
`BULL→CAUTION` 전환 주문도 그대로 유지합니다. 이 방향의 리밸런싱은 하락 후
목표비중 복귀 효과가 있었기 때문입니다.

2012-01-03~2026-07-31 동일 조건의 회고적 검증 결과는 다음과 같습니다.

| 전략 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|---:|
| 부모 전략 | 14.47% | -23.29% | 0.832 | 711 | 129 |
| 선택적 리밸런싱 | 14.55% | -23.26% | 0.836 | 371 | 95 |

3년 롤링 13개 구간에서 CAGR은 12개, MDD는 5개, Sharpe는 9개 구간에서 부모보다
높았습니다. CAGR과 MDD가 동시에 개선된 구간은 5개였습니다. 전체기간 개선 폭은
작으므로 주된 장점은 예측 규칙 추가 없이 불필요한 거래를 줄이는 데 있습니다.

## RetirementAllocationSelectiveSafeBlendStrategy

`_SafeBlendMixin`과 `RetirementAllocationSelectiveRebalanceStrategy`를 결합합니다.
BND/BIL 혼합 비중 산정은 `SafeBlendAllocationStrategy`와 같고, 혼합 목표가
변경되면 `CAUTION→BULL`이라도 주문을 유지합니다. 목표 혼합이 동일하고 5%p
밴드 안인 경우에만 상태 전환 주문을 생략합니다.

2012-01-03~2026-07-31 결과는 CAGR 14.53%, MDD -22.76%, Sharpe 0.837,
거래 574건, 리밸런싱 157회입니다. 직접 비교 대상인 `SafeBlendAllocationStrategy`
대비 CAGR은 약 0.06%p, MDD는 0.03%p, Sharpe는 0.0027 개선됐고 거래는
383건 감소했습니다. 3년 롤링 13개 구간 중 CAGR 11개, MDD 6개, Sharpe
11개 구간에서 부모보다 높았습니다.

## RetirementAllocationSelectiveVXUSStrategy

`_VXUSSubstitutionMixin`과 `RetirementAllocationSelectiveRebalanceStrategy`를
결합합니다. VXUS는 안전자산이 아니라 QQQ와 합산해 최대 70%인 위험자산이며,
BND만 대체하고 BIL은 대체하지 않습니다.

2012-01-03~2026-07-31 결과는 CAGR 14.98%, MDD -23.14%, Sharpe 0.861,
거래 396건, 리밸런싱 95회입니다. 직접 비교 대상인 `VXUSSubstitutionStrategy`
대비 CAGR은 약 0.07%p, MDD는 0.03%p, Sharpe는 0.0039 개선됐고 거래는
340건 감소했습니다. 3년 롤링 13개 구간 중 CAGR 12개, MDD 7개, Sharpe
9개 구간에서 부모보다 높았습니다.

## RetirementAllocationSelectiveSPYStrategy

`RetirementAllocationSelectiveVXUSStrategy`를 상속하고 대체 위험자산만 VXUS에서
SPY로 변경합니다. SPY는 QQQ와 합산해 최대 70%인 위험자산이며 BND만 대체하고
BIL은 대체하지 않습니다. 필요한 ticker는 `QQQ`, `BND`, `BIL`, `SPY`입니다.

2012-01-03~2026-07-31 결과는 CAGR 14.81%, MDD -23.62%, Sharpe 0.850,
거래 395건, 리밸런싱 95회입니다. 같은 선택적 주문 규칙을 사용하는 VXUS 버전과
비교하면 CAGR은 0.18%p, MDD는 0.48%p, Sharpe는 0.0117 낮았습니다. 3년 롤링
13개 구간에서 VXUS보다 CAGR이 높은 구간은 4개, MDD가 나은 구간은 0개였으므로
SPY는 대체 연구 후보로 유지하고 우선 전략으로 선택하지 않습니다.

## SafeBlendAllocationStrategy

직접 부모 로직은 `RetirementAllocationStrategy`이며 상태 판단과 QQQ 목표 비중,
전이 조건과 실행 일수를 변경하지 않습니다.

차이점은 안전자산 선택뿐입니다. BND와 BIL의 ROC40 차이에 따라 안전자산 슬리브를
다음 비율로 나눕니다.

| BND ROC40 - BIL ROC40 | 안전자산 내 BND | 안전자산 내 BIL |
|---|---:|---:|
| 1.00%p 이상 | 100% | 0% |
| 0.25%p 이상 | 75% | 25% |
| -0.25%p 이상 | 50% | 50% |
| -1.00%p 초과 | 25% | 75% |
| -1.00%p 이하 | 0% | 100% |

## VXUSSubstitutionStrategy

직접 부모 로직은 `RetirementAllocationStrategy`이며 상태 판단과 전이 조건을
변경하지 않습니다.

부모가 BND를 안전자산으로 선택했고 QQQ 비중이 70%보다 낮을 때만 BND 일부를
VXUS로 대체합니다. QQQ와 VXUS를 합친 위험자산은 최대 70%입니다.

| 상태 | QQQ | 최대 VXUS | 최소 잔여 안전자산 |
|---|---:|---:|---:|
| `BULL` | 70% | 0% | 30% |
| `CAUTION` | 70% | 0% | 30% |
| `BEAR` | 0% | 70% | 30% |
| `RECOVERY` | 50% | 20% | 30% |

BIL이 선택된 경우에는 대체하지 않습니다.

## SingleProductAllocationStrategy

`RetirementAllocationStrategy`의 상태, 전이와 목표 위험자산 비중을 그대로 사용하고
QQQ 슬리브를 실제 상품 한 종목으로 매핑하는 공통 부모 클래스입니다. QQQ는 신호
생성에 계속 사용되지만 실제 QQQ 주문은 발생하지 않습니다.

## 실제 상품 매핑 전략

아래 세 클래스는 `SingleProductAllocationStrategy`에서 상품 ticker만 변경합니다.
상태, 전이, 자산배분과 리밸런싱 규칙은 모두 부모와 같습니다.

| 클래스 | QQQ 슬리브의 실제 상품 |
|---|---|
| `KodexNasdaqAllocationStrategy` | `379810.KS` |
| `TimeNasdaqAllocationStrategy` | `426030.KS` |
| `KoActNasdaqAllocationStrategy` | `0015B0.KS` |

## NasdaqProductMixAllocationStrategy

`RetirementAllocationStrategy`를 직접 상속합니다. 상태와 전이는 부모와 같고, QQQ
위험자산 슬리브만 다음 세 상품으로 나눕니다.

| 상품 | 위험자산 슬리브 내 비중 | BULL에서 전체 비중 | RECOVERY에서 전체 비중 |
|---|---:|---:|---:|
| `379810.KS` | 50% | 35% | 25% |
| `426030.KS` | 30% | 21% | 15% |
| `0015B0.KS` | 20% | 14% | 10% |

세 상품의 합계 위험자산 비중은 항상 부모의 QQQ 목표 비중과 같습니다.
