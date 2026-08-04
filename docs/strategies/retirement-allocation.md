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

