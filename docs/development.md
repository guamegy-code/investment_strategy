# 개발 방법

[← README](../README.md)

이 프로젝트에서 개발한다는 것은 시장 판단과 자산배분 규칙을 하나의 전략 클래스로
정의하고, 공통 백테스트 환경에서 기존 전략과 비교·검증하는 것을 의미합니다.

```text
전략 정의 → 배분과 전이 설계 → 데이터 정의 → 클래스 구현
         → 제약 및 회귀 테스트 → 백테스트와 검증 → 성과 문서화
```

## 1. 전략 정의

코드를 작성하기 전에 전략이 해결하려는 문제와 비교 기준을 먼저 정합니다.

- 목표: 수익률 개선, 낙폭 방어, 회복 속도 개선 등
- 기준 전략: 새로 설계할지, 기존 전략의 변형으로 만들지
- 운용 범위: 일반 계좌인지 안전자산 제약이 있는 퇴직연금인지
- 판단 방식: 이산적인 시장 상태를 사용할지, 비중을 연속적으로 조절할지
- 비교 기준: 어떤 벤치마크 및 부모 전략과 동일 기간에 비교할지

기존 전략의 시장 판단과 실행 흐름을 유지한다면 그 전략을 상속하고 변경점만
구현합니다. 독립적인 판단 체계라면 `BaseStrategy`에서 시작합니다. 현재 클래스의
상속 관계는 [전략 클래스 문서](strategy.md)에서 확인할 수 있습니다.

## 2. 자산배분과 상태 전이 설계

전략의 핵심은 각 상황의 목표 비중과 상황이 바뀌는 조건입니다. 구현 전에 다음
내용을 표나 상태 전이도로 정리합니다.

1. 사용할 상태와 최초 상태 결정 방법
2. 상태별 위험자산, 안전자산 및 분산자산의 목표 비중
3. 각 상태에 진입하고 이탈하는 시장 조건
4. 신호가 유지되어야 하는 확인 기간
5. 정기 또는 밴드 리밸런싱 조건
6. 상태별 분할 실행 일수와 기록할 사유

예를 들어 상태 기반 전략은 다음 형태로 먼저 명세할 수 있습니다.

| 상태 | 위험자산 | 안전자산 | 진입 조건 | 실행 |
|---|---:|---:|---|---|
| 정상 | 70% | 30% | 회복 신호 확인 | 3일 분할 |
| 방어 | 30% | 70% | 위험 회피 신호 확인 | 즉시 또는 1일 |

실제 조건과 비중은 전략마다 다릅니다. 상세 전략 문서에는 모든 상태의 배분과 전이
조건을 빠짐없이 기록합니다.

### 퇴직연금 제약

퇴직연금 전략은 정상, 방어, 회복을 포함한 모든 상태에서 인정되는 안전자산을
최소 30% 보유해야 합니다.

- BND와 BIL 등 인정되는 자산만 안전자산으로 계산합니다.
- GLD는 안전자산 30%에 포함하지 않습니다.
- 위험자산 합계는 최대 70%입니다.
- 모든 목표 비중의 합계는 100%여야 합니다.

```python
assert abs(sum(target.values()) - 1.0) < 1e-9
assert target.get("BND", 0.0) + target.get("BIL", 0.0) >= 0.30
```

이 검사는 일부 상태만이 아니라 전략이 만들 수 있는 모든 목표 비중에 적용합니다.

## 3. 데이터와 자산 정의

전략은 판단에 사용하는 지수와 실제로 거래하는 상품을 구분합니다. 클래스의
`required_tickers`는 양쪽 데이터를 모두 포함해야 합니다.

기본 다운로드 대상과 기간은 `src/config.py`에서 관리합니다.

```python
TICKERS = ["QQQ", "BND", "GLD", "BIL", "QLD", "SPY"]
FX_RATE_TICKERS = ["KRW=X"]
START_DATE = "2012-01-01"
```

새 ticker를 사용할 때는 다음 순서로 준비합니다.

1. `src/config.py`의 `TICKERS`에 ticker를 추가합니다.
2. 아래 명령으로 가격과 공통 지표를 생성합니다.
3. `data/{ticker}.csv`가 생성되었는지 확인합니다.
4. 전략의 `required_tickers`에 ticker를 포함합니다.

```powershell
uv run python src/downloader.py
```

`downloader.py`는 Yahoo Finance 조정주가를 내려받고 EMA, RSI, ROC, 변동성,
낙폭 등의 지표를 계산하여 `data/{ticker}.csv`에 저장합니다. 기존 CSV는 새 데이터로
덮어씁니다. 필요한 상품의 상장일이 서로 다르면 모든 데이터가 존재하는 공통
날짜부터 백테스트가 시작됩니다.

| 디렉터리 | 용도 |
|---|---|
| `data/` | 기본 백테스트 데이터 |
| `data_extended/` | 장기 구간 검증 데이터 |
| `data_multimarket/` | 여러 시장을 이용한 검증 데이터 |
| `data_risk_assets/` | 대체 위험자산 검증 데이터 |

기본 데이터 이외의 데이터셋은 `src/validation/`의 전용 생성 및 다운로드 함수를
통해 관리합니다.

## 4. 전략 클래스 구현

모든 전략은 `BaseStrategy`의 공통 신호 형식을 사용합니다. `evaluate()`는 현재
시장과 포트폴리오를 평가하고 목표 비중과 실행 여부를 반환합니다.

```python
from strategy import BaseStrategy


class MyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.target = {"QQQ": 0.60, "GLD": 0.10, "BND": 0.30}

    @property
    def required_tickers(self):
        return tuple(self.target)

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        rebalance = month != self.last_rebalance_month
        if rebalance:
            self.last_rebalance_month = month
        return self._signal(
            rebalance=rebalance,
            target=self.target,
            days=1,
            reason="MONTHLY_REBALANCE" if rebalance else None,
        )
```

표준 신호의 필드는 다음과 같습니다.

| 필드 | 설명 |
|---|---|
| `rebalance` | 목표 비중으로 리밸런싱할지 여부 |
| `target` | ticker별 목표 비중 |
| `days` | 분할 실행 일수 |
| `reason` | 상태 전이 또는 리밸런싱 사유 |

상태 기반 전략은 상태 판정, 목표 비중 계산과 실행 판단을 별도 메서드로 나누는 것이
좋습니다. 이렇게 하면 전이 조건과 상태별 비중을 독립적으로 테스트할 수 있습니다.

### 기존 전략 상속

부모의 `evaluate()`를 그대로 사용할 수 있다면 신호 로직을 복제하지 않습니다.
자산 구성, 임계값 또는 목표 비중처럼 달라지는 부분만 재정의합니다. 파생 전략의
테스트는 부모에게서 달라진 동작에 집중하고, 문서에는 변경된 동작뿐 아니라 변경
목적과 수익률·위험·거래에 미치는 영향 및 트레이드오프를 함께 기록합니다.

### 실제 상품 매핑

기준 지수의 신호를 실제 상품으로 실행할 때는 `asset_mapping`을 사용합니다.

```python
from strategy import RetirementAllocationStrategy


class MyPensionProductStrategy(RetirementAllocationStrategy):
    def __init__(self):
        super().__init__(
            signal_asset="QQQ",
            asset_mapping={
                "QQQ": {"MY_PRODUCT.KS": 1.0},
                "BND": {"BND": 1.0},
                "BIL": {"BIL": 1.0},
            },
        )
```

상품 매핑 후에도 목표 비중 합계와 위험·안전자산 한도가 원본 전략의 제약을
유지하는지 확인합니다.

## 5. 실행 대상 등록과 백테스트

비교할 전략은 `src/main.py`의 `strategies` 튜플에 등록합니다. Runner는 각 전략의
`required_tickers`에 따라 필요한 데이터 파일을 선택하고 동일한 실행 조건으로
백테스트합니다.

```powershell
uv run python src/main.py
```

`main.py`는 활성 전략의 `required_tickers`와 `FX_RATE_TICKERS`를 모아 필요한 CSV 파일을 먼저 확인합니다. `data/{ticker}.csv`가 없으면 해당 티커만 Yahoo Finance에서 자동 다운로드하며, 이미 존재하는 CSV는 갱신하거나 덮어쓰지 않습니다. 누락 자료를 받을 때는 인터넷 연결이 필요합니다. 전체 CSV를 강제로 갱신하려면 다음 명령을 사용합니다.

```powershell
uv run python src/downloader.py
```

기본 실행 조건은 `src/config.py`에서 관리합니다.

```text
시작일: 2012-01-01
신호: 거래일 종가
체결: 다음 거래일 시가
수수료: 0.015%
슬리피지: 0.020%
```

실행 결과는 `results/`에 저장됩니다.

```text
results/
├── MyStrategy_history.csv  # 일별 포트폴리오 가치, 비중과 상태
├── MyStrategy_trades.csv   # 거래 가격, 수량과 비용
└── strategy_comparison.png # 전략 비교 차트
```

## 6. 테스트와 검증

먼저 전략의 규칙이 설계대로 작동하는지 단위 및 회귀 테스트로 확인합니다.

```powershell
$env:MPLBACKEND = "Agg"
uv run python -m unittest discover -s tests
```

새 전략에는 최소한 다음 항목을 검증하는 테스트를 추가합니다.

- 필요한 ticker와 신호·매매 자산의 포함 여부
- 모든 상태의 목표 비중 합계
- 퇴직연금 안전자산 30% 하한과 위험자산 70% 상한
- 상태 진입, 방어 및 회복 조건과 확인 기간
- 불필요한 중복 리밸런싱 여부
- 상품 매핑 후의 목표 비중과 자산 한도

단일 백테스트의 CAGR과 MDD만으로 전략을 선택하지 않습니다. 기존 전략과 같은
기간 및 비용 조건에서 비교한 뒤 고정 구간, 롤링 구간, 임계값 민감도와 OOS 검증을
수행합니다. 검증 도구는 `src/validation/`에 있습니다.

### 온라인 확장학습 전략 주의사항

`EXPANDING_RISK_FORECAST_30_70`처럼 전략 객체가 실행 중 직접 학습하는 경우에는 현재
시점에서 결과가 완전히 확정된 관측치만 학습 집합에 넣어야 합니다. 이 전략은 월별 특징을
기록한 뒤 21거래일이 지난 시점에 하방변동성과 최대 경로손실을 계산합니다. 실행 시작 전
데이터로 모델을 미리 적합하지 않으므로 최소 60개월 동안은 70/30 목표를 사용합니다.

`src/validation/continuous_risk_forecast.py`의 장기 확장 데이터 검증과 `src/main.py`의
일반 실행은 워밍업 데이터 범위가 다를 수 있습니다. 결과를 비교할 때는 모델 활성 시작일과
학습 표본 수를 함께 확인해야 합니다.

## 7. 성과 확인과 문서화

성과표와 비교 그래프에서 수익률뿐 아니라 낙폭, 위험조정 성과와 거래 특성을 함께
확인합니다. 실제 상품 전략은 상장일이 달라질 수 있으므로 기간이 다른 CAGR을 직접
비교하지 않습니다.

새 전략을 추가한 뒤 [전략 클래스 문서](strategy.md)의 전체 상속 트리와 통합
성과표를 갱신합니다. 주요 전략의 상세 문서에는 다음 내용을 기록합니다.

1. 전략의 목적과 직접 부모 클래스
2. 조합한 Mixin의 역할, 변경 동작과 적용 순서
3. 상태별 목표 비중과 인정 안전자산 비중
4. 상태 진입·이탈 조건과 확인 기간
5. 리밸런싱 밴드, 실행 일수와 신호 사유
6. 기준 지수와 실제 상품의 매핑
7. 부모 대비 변경점, 변경 목적과 성과·위험·거래 트레이드오프

부모 전략을 상속한 전략은 공통 로직을 반복하지 않고 부모와 달라진 부분을 중심으로
기술하되, 변경 이유와 운용상 의미를 생략하지 않습니다.
