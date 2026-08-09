# RetirementAllocationStrategy와 상품 매핑

[← 전체 전략](../strategy.md)

## RetirementAllocationLegacyStrategy

이 클래스는 이름 변경 전 `RetirementAllocationStrategy`의 비선택형 주문 동작을
보존합니다. OOS 잠금과 과거 비교를 위한 기준이며 `main.py`의 활성 전략에는
포함하지 않습니다. `BaseStrategy`의 공통 실행·상품 매핑 위에 퇴직연금 상태,
위험자산 한도와 안전자산 선택을 구현한 최초 기준을 보존하는 것이 목적이며,
중복 주문이 많더라도 과거 OOS 결과를 재현할 수 있다는 의미가 있습니다.

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

## RetirementAllocationStrategy

`RetirementAllocationLegacyStrategy`를 직접 상속하며 상태 판단, 확인 기간, 상태별 목표
비중, 안전자산 선택과 실행 일수는 부모와 같습니다. 차이는 `CAUTION→BULL` 전환
시 실제 주문을 만들지 여부뿐입니다. 목표가 실질적으로 달라지지 않는 전환의 거래비용을
줄이는 것이 변경 목적이며, 그 대신 밴드 안의 작은 비중 오차는 그대로 유지합니다.

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
| `RetirementAllocationLegacyStrategy` | 14.47% | -23.29% | 0.832 | 711 | 129 |
| `RetirementAllocationStrategy` | 14.55% | -23.26% | 0.836 | 371 | 95 |

3년 롤링 13개 구간에서 CAGR은 12개, MDD는 5개, Sharpe는 9개 구간에서 부모보다
높았습니다. CAGR과 MDD가 동시에 개선된 구간은 5개였습니다. 전체기간 개선 폭은
작으므로 주된 장점은 예측 규칙 추가 없이 불필요한 거래를 줄이는 데 있습니다.

이 전략은 이전의 `RetirementAllocationSelectiveRebalanceStrategy`와 같은
동작입니다. 긴 이름은 외부 import 호환을 위한 래퍼로만 유지하고 신규 코드와
`main.py`에서는 `RetirementAllocationStrategy`를 사용합니다.

## 공통 Mixin

아래 Mixin은 단독으로 실행하지 않고 `RetirementAllocationStrategy` 계열에 조합합니다.
각 Mixin은 목표 계산이나 주문 판단의 한 단계만 담당하므로, 상태 전이처럼 명시적으로
바꾸지 않은 동작은 최종 부모 전략을 그대로 사용합니다.

### SafeBlend Mixin (`_SafeBlendMixin`)

매월 BND와 BIL의 ROC40 차이를 계산하고 안전자산 슬리브를 25% 단위로 나눕니다.
위험자산 목표는 바꾸지 않습니다.

| BND ROC40 - BIL ROC40 | 안전자산 내 BND | 안전자산 내 BIL |
|---|---:|---:|
| 1.00%p 이상 | 100% | 0% |
| 0.25%p 이상 | 75% | 25% |
| -0.25%p 이상 | 50% | 50% |
| -1.00%p 초과 | 25% | 75% |
| -1.00%p 이하 | 0% | 100% |

기본 Mixin은 이 혼합을 모든 상태에 적용합니다. BND/BIL 중 하나를 전량 선택하는
시점 위험을 줄이는 것이 목적이지만 혼합 비중이 달라질 때 주문이 추가되고, 더 약한
안전자산을 일부 계속 보유할 수 있습니다.

### Defensive SafeBlend Mixin (`_DefensiveSafeBlendMixin`)

`_SafeBlendMixin`을 상속하되 위 혼합 목표를 `BEAR`와 `RECOVERY`에만 적용합니다.
혼합 비중 자체는 매월 계산하지만 BULL/CAUTION에서는 부모의 0.25%p 버퍼가 있는
단일 BND/BIL 선택을 사용합니다. 상승 국면의 기존 수익 특성과 거래 빈도를 보존하는
것이 목적이며, 그 구간에서는 혼합에 따른 분산 효과를 얻지 못합니다.

### VXUS Substitution Mixin (`_VXUSSubstitutionMixin`)

부모가 목표를 계산한 다음 QQQ 목표가 70%보다 낮고 BND가 남아 있으면 다음 순서로
VXUS를 편입합니다.

```text
위험 한도 여유 = 70% - QQQ 목표
VXUS 목표 = min(BND 목표, 위험 한도 여유)
BND 목표 = 기존 BND 목표 - VXUS 목표
```

BIL은 대체하지 않으며 QQQ와 VXUS 합계가 70%를 넘으면 오류로 처리합니다. 남는
위험 한도를 해외주식으로 활용하는 것이 목적이지만 방어 국면의 주식 노출과 추가
거래가 늘어납니다. `ALTERNATIVE_RISK_ASSET`을 SPY로 바꾼 하위 클래스도 같은 계산을
재사용합니다.

### Upper Risk Band Mixin (`_UpperRiskBandMixin`)

BULL/CAUTION에서 QQQ 또는 QQQ에 매핑된 실제 상품 비중이 기본 목표 70%를 넘으면
다음과 같이 최종 주문 신호를 조정합니다.

```text
실제 QQQ 비중 <= 70%      → 부모의 복원·매수 주문 유지
70% < 실제 QQQ 비중 < 80% → 상태·월간 밴드 매도 생략
                             안전자산 교체 시 QQQ는 유지하고 안전자산만 거래
실제 QQQ 비중 >= 80%      → 부모의 전체 목표로 복원
BEAR/RECOVERY              → 부모의 전체 목표 적용
```

상승 이익을 상단까지 보유하는 것이 목적이며, 상단값은 생성자 인자로 바꿀 수 있습니다.
그 대신 위험자산 집중과 MDD가 기본 전략보다 커질 수 있습니다.

### 조합 순서

`RetirementAllocationProfitBandVXUSStrategy`의 목표와 주문은 다음 순서로 만들어집니다.

```text
RetirementAllocationStrategy가 상태별 기본 목표 계산
→ _DefensiveSafeBlendMixin이 BEAR/RECOVERY의 BND/BIL 혼합
→ _VXUSSubstitutionMixin이 남은 BND를 위험 한도 안에서 VXUS로 대체
→ 지수 목표를 실제 상품으로 매핑
→ _UpperRiskBandMixin이 BULL/CAUTION의 최종 주문 여부와 보존 목표 조정
```

따라서 방어 국면에서는 먼저 안전자산을 혼합한 뒤 남은 BND만 VXUS로 바뀌고,
상승 국면의 안전자산 교체에서는 QQQ 초과 수익을 유지할 수 있습니다.

## RetirementAllocationProfitBandStrategy

후보 전략 모듈의 `_UpperRiskBandMixin`, `_DefensiveSafeBlendMixin`과
`RetirementAllocationStrategy`를 결합합니다. 상승 국면에는 광범위한 상단 밴드
검증에서 선택한 80% 상한을 적용하고, 방어 국면에는 40거래일 BND/BIL 모멘텀
차이에 따른 25% 단위 혼합을 적용합니다. 상승 추세에서 QQQ 이익을 너무 일찍
실현하지 않으면서 방어 국면의 안전자산 선택 위험을 완화하는 것이 목적입니다.
상승 수익 참여와 거래 감소를 기대하는 대신 QQQ 집중도가 80%까지 높아져 MDD가
기본 전략보다 커질 수 있습니다.

- `BULL` 또는 `CAUTION`에서 QQQ가 70% 이하이면 부모 전략의 70% 복원 매수를
  그대로 실행합니다.
- QQQ가 70% 초과 80% 미만이면 동일 목표 상태 전환과 월간 밴드 주문에서도
  QQQ를 70%로 복원하지 않고 초과 수익을 방치합니다.
- 이 구간에서 BND/BIL 교체가 필요하면 QQQ 현재 비중을 유지하고 나머지
  안전자산 슬리브만 새 BND 또는 BIL로 이동합니다.
- QQQ가 80% 이상이면 전체 목표 70/30으로 복원합니다.
- `BEAR` 또는 `RECOVERY`처럼 위험 목표가 실제로 달라지는 전환은 부모의 전체
  목표 비중을 적용합니다.
- `BEAR`와 `RECOVERY`에서는 안전자산을 BND 또는 BIL 하나로 고르지 않고
  BND/BIL 비중을 0/25/50/75/100% 단위로 나눕니다. BULL/CAUTION의 안전자산
  선택에는 기존 버퍼 규칙을 유지합니다.

### 성과 비교

2012-01-03~2026-07-31 동일 조건의 회고적 결과입니다.

| 비교 전략 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|---:|
| `RetirementAllocationStrategy` | 14.55% | -23.26% | 0.836 | 371 | 95 |
| 상단 밴드만 적용한 이전 후보 | 15.12% | -24.02% | 0.845 | 265 | 65 |
| `RetirementAllocationProfitBandStrategy` | 15.15% | -23.64% | 0.846 | 271 | 67 |

기본 전략보다 CAGR은 약 0.61%p 높지만 MDD는 약 0.38%p 확대됐습니다. 방어 혼합은
상단 밴드만 적용한 후보보다 CAGR과 MDD를 모두 개선한 대신 거래 6건과 리밸런싱
2회를 추가했습니다. 2024년 이후에는 방어 상태가 발생하지 않아 이전 후보와 같은
CAGR 18.79%, MDD -17.39%입니다.

## RetirementAllocationProfitBandVXUSStrategy

`_VXUSSubstitutionMixin`과 `RetirementAllocationProfitBandStrategy`를
결합합니다. BULL/CAUTION에서는 부모의 80% QQQ 수익방치 규칙을 적용합니다.
BEAR/RECOVERY에서는 먼저 BND/BIL을 혼합하고, 그중 남은 BND 일부만 VXUS로
대체합니다. VXUS는 안전자산이 아니라 위험자산으로 집계합니다. QQQ 상단 수익방치에
해외주식 분산을 더해 남는 위험 한도를 활용하는 것이 변경 목적입니다. 수익원은
늘어나지만 QQQ와 VXUS의 동반 하락 및 해외주식 노출이 추가되는 트레이드오프가
있습니다.

- BULL/CAUTION에서 QQQ 70~80%는 상태·밴드 주문에서도 초과분을 유지합니다.
- 같은 구간의 BND/BIL 교체에서는 QQQ를 유지하고 안전자산 슬리브만 교체합니다.
- QQQ 80% 이상에서는 70%로 복원합니다.
- BEAR/RECOVERY에서는 방어 혼합 후 남아 있는 BND에만 기존 상태의 VXUS 목표를
  적용하며 BIL은 대체하지 않습니다.

### 성과 비교

2012-01-03~2026-07-31 동일 조건의 회고적 결과입니다.

| 비교 전략 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|---:|
| `RetirementAllocationVXUSStrategy` | 15.05% | -22.42% | 0.864 | 403 | 97 |
| 상단 밴드+VXUS 이전 후보 | 15.56% | -23.91% | 0.869 | 290 | 65 |
| `RetirementAllocationProfitBandVXUSStrategy` | 15.63% | -23.20% | 0.872 | 297 | 67 |

VXUS 전략보다 CAGR은 약 0.58%p 높지만 MDD는 약 0.78%p 확대됐습니다. 방어 혼합은
이전 상단 밴드+VXUS 후보보다 CAGR, MDD와 Sharpe를 모두 개선한 대신 거래 7건과
리밸런싱 2회를 추가했습니다.

이전 이름인 `RetirementAllocationSafeSleeveOnlyStrategy`와
`RetirementAllocationSafeSleeveOnlyVXUSStrategy`는 외부 import 호환을 위한
래퍼로만 유지합니다. 신규 코드와 `main.py`에서는 `ProfitBand` 이름을 사용합니다.

## RetirementAllocationSafeBlendStrategy

`_SafeBlendMixin`과 `RetirementAllocationStrategy`를 결합합니다.
BND/BIL 혼합 비중 산정은 `SafeBlendAllocationStrategy`와 같고, 혼합 목표가
변경되면 `CAUTION→BULL`이라도 주문을 유지합니다. 목표 혼합이 동일하고 5%p
밴드 안인 경우에만 상태 전환 주문을 생략합니다. BND/BIL을 하나로 전환하는 시점
위험을 줄이는 것이 목적이지만 모든 상태에서 혼합을 다시 계산하므로 리밸런싱 횟수가
늘 수 있습니다.

### 성과 비교

| 비교 전략 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|---:|
| `SafeBlendAllocationStrategy` | 14.50% | -22.90% | 0.834 | 717 | 131 |
| `RetirementAllocationSafeBlendStrategy` | 14.53% | -22.76% | 0.837 | 574 | 157 |

직접 비교 전략보다 CAGR, MDD와 Sharpe가 소폭 개선되고 거래는 143건 감소했지만,
혼합 목표 변경 때문에 리밸런싱은 26회 증가했습니다. 3년 롤링 13개 구간에서는
CAGR 4개, MDD 7개, Sharpe 7개 구간에서 더 높았습니다. 혼합을 방어 국면으로
제한할지는 별도 후보로 검증합니다.

## RetirementAllocationVXUSStrategy

`_VXUSSubstitutionMixin`, `_DefensiveSafeBlendMixin`과
`RetirementAllocationStrategy`를 결합합니다. BULL/CAUTION은
기존 단일 안전자산 선택을 유지하고, BEAR/RECOVERY에서는 BND/BIL을 먼저
혼합한 뒤 남은 BND만 VXUS로 대체합니다. VXUS는 안전자산이 아니라 QQQ와
합산해 최대 70%인 위험자산이며 BIL은 대체하지 않습니다. QQQ가 줄어드는 방어·회복
국면에서 남는 위험 한도를 해외주식으로 분산 활용하되, 현금성 BIL의 방어 역할은
훼손하지 않는 것이 변경 목적입니다. 기대수익과 분산 기회가 늘지만 주식시장 동반
하락 위험과 추가 거래가 발생합니다.

### 성과 비교

| 비교 전략 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|---:|
| 방어 혼합 전 VXUS 후보 | 14.98% | -23.14% | 0.861 | 396 | 95 |
| `RetirementAllocationVXUSStrategy` | 15.05% | -22.42% | 0.864 | 403 | 97 |

방어 혼합으로 CAGR은 약 0.06%p, MDD는 약 0.72%p, Sharpe는 약 0.003 개선됐고
거래 7건과 리밸런싱 2회가 늘었습니다. 차이가 발생한 3년 롤링 6개 구간에서는
CAGR이 3개 구간에서 높고 3개에서 낮았지만 전체 평균 세 지표는 개선됐습니다.
2024년 이후 결과는 이전 방식과 동일합니다.

## RetirementAllocationSPYStrategy

`RetirementAllocationVXUSStrategy`를 상속하고 대체 위험자산만 VXUS에서
SPY로 변경합니다. 따라서 BEAR/RECOVERY 전용 SafeBlend도 상속합니다. SPY는
QQQ와 합산해 최대 70%인 위험자산이며 혼합 후 남은 BND만 대체하고 BIL은
대체하지 않습니다. 필요한 ticker는 `QQQ`, `BND`, `BIL`, `SPY`입니다. VXUS의
지역 분산 대신 미국 대형주 노출을 사용했을 때의 효과를 비교하는 것이 변경
목적이며, QQQ와의 상관이 더 높아 분산 효과가 약해질 수 있습니다.

### 부모 대비 성과

| 전략 | 대체 위험자산 | CAGR | MDD | Sharpe | 거래 수 | 리밸런싱 수 |
|---|---|---:|---:|---:|---:|---:|
| `RetirementAllocationVXUSStrategy` | VXUS | 15.05% | -22.42% | 0.864 | 403 | 97 |
| `RetirementAllocationSPYStrategy` | SPY | 14.90% | -22.60% | 0.854 | 402 | 97 |

부모보다 CAGR은 약 0.14%p, MDD는 약 0.18%p, Sharpe는 약 0.010 낮았습니다.
따라서 SPY는 대체 연구 후보로 유지하고 우선 전략으로 선택하지 않습니다.

이전 이름인 `RetirementAllocationSelectiveSafeBlendStrategy`,
`RetirementAllocationSelectiveVXUSStrategy`,
`RetirementAllocationSelectiveSPYStrategy`는 외부 import 호환용 래퍼로만
유지합니다. 신규 코드와 `main.py`에서는 `Selective`가 없는 이름을 사용합니다.

## SafeBlendAllocationStrategy

직접 부모 로직은 `RetirementAllocationLegacyStrategy`이며 상태 판단과 QQQ 목표 비중,
전이 조건과 실행 일수를 변경하지 않습니다. 안전자산을 한 번에 전량 교체할 때의
시점 위험을 줄이기 위해 방어 국면에서만 BND/BIL을 단계적으로 혼합합니다. 혼합으로
방어 안정성을 높일 수 있지만 목표 변화와 주문이 추가될 수 있습니다.

`BULL`과 `CAUTION`에서는 부모의 버퍼가 있는 BND/BIL 단일 선택을 그대로
사용합니다. `BEAR`와 `RECOVERY`에서만 BND와 BIL의 ROC40 차이에 따라
안전자산 슬리브를 [공통 Mixin](#공통-mixin)의 25% 단위 비율로 나눕니다.

### 부모·이전 방식 대비 성과

| 비교 전략 | CAGR | MDD | 거래 수 | 리밸런싱 수 |
|---|---:|---:|---:|---:|
| `RetirementAllocationLegacyStrategy` | 14.47% | -23.29% | 711 | 129 |
| 모든 상태 SafeBlend 이전 방식 | 14.47% | -22.80% | 957 | 192 |
| `SafeBlendAllocationStrategy` | 14.50% | -22.90% | 717 | 131 |

방어 국면으로 혼합을 제한하면서 이전 방식보다 CAGR은 약 0.03%p 높아지고 거래
240건과 리밸런싱 61회가 줄었습니다. MDD는 이전 방식보다 약 0.10%p 확대됐지만
직접 부모보다는 약 0.39%p 개선됐습니다. 3년 롤링 13개 구간 중 이전 방식보다
CAGR은 10개, Sharpe는 9개 구간에서 높았습니다.

## VXUSSubstitutionStrategy

직접 부모 로직은 `RetirementAllocationLegacyStrategy`이며 상태 판단과 전이 조건을
변경하지 않습니다. QQQ 축소 상태에서 사용하지 않는 위험자산 한도를 해외주식으로
분산 활용해 기대수익을 높이는 것이 목적입니다. 대신 부모보다 주식 노출이 커지고
VXUS 거래 및 동반 하락 위험이 추가됩니다.

부모가 BND를 안전자산으로 선택했고 QQQ 비중이 70%보다 낮을 때만 BND 일부를
VXUS로 대체합니다. QQQ와 VXUS를 합친 위험자산은 최대 70%입니다.

| 상태 | QQQ | 최대 VXUS | 최소 잔여 안전자산 |
|---|---:|---:|---:|
| `BULL` | 70% | 0% | 30% |
| `CAUTION` | 70% | 0% | 30% |
| `BEAR` | 0% | 70% | 30% |
| `RECOVERY` | 50% | 20% | 30% |

BIL이 선택된 경우에는 대체하지 않습니다.

### 부모 대비 성과

| 전략 | CAGR | MDD |
|---|---:|---:|
| `RetirementAllocationLegacyStrategy` | 14.47% | -23.29% |
| `VXUSSubstitutionStrategy` | 14.91% | -23.18% |

부모보다 CAGR은 약 0.44%p, MDD는 약 0.11%p 개선됐습니다. 다만 선택적 주문
생략과 방어 국면 SafeBlend가 없는 레거시 비교 전략이므로 현재 기본 확장으로
사용하지 않습니다.

## SingleProductAllocationStrategy

`RetirementAllocationProfitBandVXUSStrategy`의 80% 상단 수익방치, 선택적 주문
생략, 방어 국면 SafeBlend와 VXUS 대체를 사용하고 QQQ 슬리브를 실제 상품 한
종목으로 매핑하는 공통 부모 클래스입니다. QQQ는 신호 생성에 계속 사용되지만 실제
QQQ 주문은 발생하지 않으며, VXUS는 별도의 위험자산으로 거래됩니다. 정상 목표에서
QQQ 상품과 VXUS의 합계는 70%이고 상승 중에는 상단 밴드까지 초과분을 유지합니다.
지수 신호를 유지하면서 실제 퇴직연금 상품으로 체결하기 위한 변경입니다. 전략 규칙은
재사용할 수 있지만 상품 추적오차, 상장 이후의 짧은 검증 기간과 VXUS 직접 거래
가능 여부를 별도로 고려해야 합니다.

| 항목 | 부모 전략 | `SingleProductAllocationStrategy` |
|---|---|---|
| 시장 판단 | QQQ | QQQ로 동일 |
| QQQ 슬리브 체결 | QQQ | 생성 시 선택한 실제 상품 한 종목 |
| ProfitBand·SafeBlend·VXUS | 적용 | 동일하게 적용 |
| 추가 고려사항 | 지수 ETF 자체 성과 | 상품 추적오차·상장일·거래 가능 여부 |

## 실제 상품 매핑 전략

아래 세 클래스는 `SingleProductAllocationStrategy`에서 상품 ticker만 변경합니다.
상태, 전이, 자산배분과 리밸런싱 규칙은 모두 부모와 같습니다. 동일한 전략을 서로
다른 나스닥 상품으로 실행했을 때의 추적 특성과 성과를 비교하는 것이 변경 목적입니다.
상품별 운용 방식과 상장일이 다르므로 성과 차이를 전략 규칙의 우열로 해석할 수는
없습니다.

| 클래스 | QQQ 슬리브 상품 | 검증 기간 | CAGR | MDD |
|---|---|---|---:|---:|
| `KodexNasdaqAllocationStrategy` | `379810.KS` | 2021-04-09~2026-07-31 | 17.75% | -18.18% |
| `TimeNasdaqAllocationStrategy` | `426030.KS` | 2022-05-11~2026-07-31 | 34.07% | -27.23% |
| `KoActNasdaqAllocationStrategy` | `0015B0.KS` | 2025-02-25~2026-07-31 | 46.31% | -26.11% |

RECOVERY에서는 QQQ 상품 50%, VXUS 20%, 안전자산 30%가 기본 목표입니다.
검증 시작일이 서로 다르므로 표의 CAGR과 MDD를 상품 간 우열로 직접 비교해서는
안 됩니다.

## NasdaqProductMixAllocationStrategy

`RetirementAllocationProfitBandVXUSStrategy`를 직접 상속합니다. 80% 상단
수익방치, 선택적 주문 생략, 방어 국면 SafeBlend와 VXUS 대체는 부모와 같고,
QQQ 위험자산 슬리브만 다음 세 상품으로 나눕니다. 단일 상품의 추적·운용사 위험을
분산하는 것이 변경 목적입니다. 상품별 성과 차이를 평균화할 수 있지만 거래 종목과
관리 복잡성이 늘고, 가장 성과가 좋은 한 상품에 집중했을 때보다 수익이 낮을 수
있습니다.

| 항목 | 부모 전략 | `NasdaqProductMixAllocationStrategy` |
|---|---|---|
| 시장 판단 | QQQ | QQQ로 동일 |
| QQQ 슬리브 체결 | QQQ 한 종목 | 국내 나스닥 상품 3종 |
| 슬리브 내부 비중 | 100% | 50% / 30% / 20% |
| ProfitBand·SafeBlend·VXUS | 적용 | 동일하게 적용 |
| 주요 트레이드오프 | 단순한 관리 | 상품 위험 분산, 종목·주문 복잡성 증가 |

| 상품 | 위험자산 슬리브 내 비중 | BULL에서 전체 비중 | RECOVERY에서 전체 비중 |
|---|---:|---:|---:|
| `379810.KS` | 50% | 35% | 25% |
| `426030.KS` | 30% | 21% | 15% |
| `0015B0.KS` | 20% | 14% | 10% |

세 상품의 합계 위험자산 비중은 항상 부모의 QQQ 목표 비중과 같습니다.
RECOVERY에서는 세 상품 합계 50%와 VXUS 20%를 합쳐 위험자산 70%가 됩니다.
2025-02-25~2026-07-31 재계산 결과는 CAGR 31.44%, MDD -18.71%입니다.
