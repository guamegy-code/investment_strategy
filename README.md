# Investment Strategy

ETF 가격 데이터를 이용해 자산배분 전략을 구현하고, 동일한 백테스트 엔진으로
성과와 거래 내역을 비교하는 프로젝트입니다. 시장 상태에 따라 비중을 조정하는
퇴직연금 전략과 실제 국내 상품을 매핑한 전략을 함께 제공합니다.

이 저장소의 백테스트 결과는 전략 연구와 개발을 위한 자료이며 투자 권유가
아닙니다. 과거 성과는 미래 성과를 보장하지 않습니다.

## 주요 특징

- 여러 전략을 동일한 조건으로 백테스트하고 성과표와 비교 그래프로 시각화
- CAGR, MDD, Sharpe, Sortino, Calmar 등 핵심 성과 지표 계산
- 다음 거래일 시가 체결과 수수료·슬리피지를 반영한 실행 모델
- 일별 포트폴리오, 거래 내역과 리밸런싱 원인을 기록하여 결과 추적
- 고정 구간, 롤링 구간, 민감도 및 OOS 검증 도구 제공
- 여러 거래일에 걸친 분할 리밸런싱 지원
- 기준 지수의 신호와 실제 퇴직연금 상품을 분리하여 자산 매핑

## 프로젝트 구조

```text
investment_strategy/
├── src/
│   ├── strategy.py                 # 공통 인터페이스와 주요 전략
│   ├── experimental_strategies.py  # 주요 전략에서 파생된 후보 전략
│   ├── pension_strategies.py       # 실제 퇴직연금 상품 매핑
│   ├── downloader.py               # 가격 데이터와 지표 생성
│   ├── backtest.py                 # 백테스트 실행 엔진
│   ├── portfolio.py                # 주문, 포지션과 거래비용
│   ├── performance.py              # 성과 지표
│   ├── runner.py                   # 여러 전략 실행 및 비교
│   ├── main.py                     # 기본 실행 진입점
│   └── validation/                 # 구간, 민감도 및 OOS 검증
├── tests/                          # 단위 및 회귀 테스트
├── data/                           # 기본 시장 데이터
├── data_extended/                  # 장기 검증 데이터
├── results/                        # 백테스트 결과와 차트
├── validation/                     # OOS 잠금 파일
├── docs/
│   ├── strategy.md                 # 전체 상속 관계와 통합 성과
│   ├── development.md              # 전략 설계, 구현, 검증과 문서화
│   └── strategies/                 # 전략별 상태, 전이와 배분 로직
└── README.md
```

## 실행

```powershell
uv run python src/main.py
```

`main.py`는 실행할 전략에 필요한 티커와 `FX_RATE_TICKERS`를 확인하고 `data/{ticker}.csv`가 없을 때만 Yahoo Finance에서 자동으로 내려받습니다. 이미 있는 CSV 파일은 그대로 유지합니다. 전체 데이터를 새로 받고 싶다면 `uv run python src/downloader.py`를 별도로 실행하세요.

## 개발 방법

이 프로젝트에서 개발의 기본 단위는 자산배분 전략입니다. 전략은 시장을 어떤
상태로 판단할지, 상태마다 어떤 자산을 얼마나 보유할지, 언제 리밸런싱할지를
정의합니다. 백테스트 엔진은 모든 전략의 신호를 동일한 체결 조건으로 실행하여
성과와 거래 내역을 비교합니다.

모든 전략은 `BaseStrategy`의 인터페이스를 따릅니다. 새로운 아이디어는 직접 전략
클래스로 구현할 수 있고, 기존 전략의 판단 로직을 재사용한다면 해당 클래스를
상속하여 차이만 구현할 수 있습니다. 실제 퇴직연금 상품 전략은 원본 전략의 신호와
상품 매핑을 분리합니다.

전략 개발은 다음 흐름으로 진행합니다.

1. 전략의 목적과 기준 전략을 정합니다.
2. 자산배분 상태, 상태 전이와 리밸런싱 규칙을 설계합니다.
3. 필요한 시장 데이터와 실제 매매 자산을 정의합니다.
4. 전략 클래스를 구현하고 퇴직연금 등의 운용 제약을 검사합니다.
5. 동일 조건의 백테스트와 구간·민감도·OOS 검증을 수행합니다.
6. 상속 관계, 성과와 부모 전략과의 차이를 문서화합니다.

구체적인 인터페이스, 데이터 준비, 상품 매핑, 테스트와 실행 방법은
[개발 방법 문서](docs/development.md)를 참고하세요. 전체 클래스 관계와 현재 성과는
[전략 클래스 문서](docs/strategy.md)에서 확인할 수 있습니다.

## 선택적 리밸런싱 퇴직연금 전략

`RetirementAllocationSelectiveRebalanceStrategy`는 기존
`RetirementAllocationStrategy`의 상태 판단과 목표 비중을 그대로 사용합니다.
`CAUTION→BULL` 전환에서 목표와 안전자산이 같고 실제 비중이 5%p 밴드 안이면
상태만 갱신하고 주문을 생략합니다. 밴드 이탈, 안전자산 교체 및
`BULL→CAUTION` 전환 주문은 기존대로 실행합니다.

2012-01-03~2026-07-31 검증에서 부모 전략 대비 거래 수는 711건에서 371건으로
줄었고 CAGR은 14.47%에서 14.55%, MDD는 -23.29%에서 -23.26%로 변했습니다.
상세 규칙과 롤링 검증은
[퇴직연금 전략 문서](docs/strategies/retirement-allocation.md#retirementallocationselectiverebalancestrategy)에
정리되어 있습니다.

같은 주문 생략 규칙을 안전자산 혼합과 VXUS 대체에 각각 결합한
`RetirementAllocationSelectiveSafeBlendStrategy`와
`RetirementAllocationSelectiveVXUSStrategy`도 제공합니다. 2026-07-31까지의
동일 조건에서 두 전략 모두 각 직접 부모보다 CAGR, MDD와 Sharpe가 개선됐으며,
선택적 VXUS 전략은 CAGR 14.98%, MDD -23.14%, Sharpe 0.861을 기록했습니다.

VXUS 대신 SPY를 위험자산 여유분에 편입하는
`RetirementAllocationSelectiveSPYStrategy`도 제공합니다. SPY는 안전자산이
아니며 QQQ와 합산해 목표 위험비중 70%를 넘지 않습니다. 동일 기간 성과는 CAGR
14.81%, MDD -23.62%, Sharpe 0.850으로 선택적 VXUS 전략보다 낮았습니다.

## 확장학습 연속위험 전략

`EXPANDING_RISK_FORECAST_30_70`은 향후 21거래일의 하방변동성과 최대 경로손실을
월 1회 예측하여 QQQ 목표 비중을 30~70% 사이에서 연속적으로 조절합니다. 나머지
비중은 BND와 BIL에 절반씩 배분합니다.

- 완전히 확정된 과거 21거래일 경로만 학습하므로 미래 정보 누수를 사용하지 않습니다.
- 최소 60개의 월별 학습 표본이 쌓이기 전에는 정적 70/30 목표를 유지합니다.
- 목표에서 5%p 이상 벗어난 경우에만 3거래일에 걸쳐 리밸런싱합니다.
- 목표 위험자산 비중의 상한은 70%입니다. 가격 변동에 따른 실제 평가비중의 일시적
  초과까지 강제로 해소하는 일별 캡 전략은 아닙니다.
- 개발 구간에서는 static 7:3보다 개선됐지만 후속 검증 구간의 CAGR과 Sharpe가
  낮아졌습니다. 활성 목록에 포함된 연구 전략이며 투자 권유나 검증 완료 전략이 아닙니다.

계산식과 실행 규칙은
[확장학습 연속위험 전략 문서](docs/strategies/expanding-risk-forecast.md)에 정리되어 있습니다.
