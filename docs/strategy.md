# 전략 클래스

[← README](../README.md)

이 문서는 전략 클래스의 전체 관계와 동일한 백테스트 엔진으로 계산한 성과를 한
곳에서 보여줍니다. 자산배분 상태, 상태 전이와 리밸런싱 규칙은 전략 이름의 상세
문서 링크에서 확인할 수 있습니다.

## 전체 클래스 관계

아래 트리는 실제 Python 상속 관계를 기준으로 합니다.

```text
BaseStrategy
├── RetirementAllocationStrategy
│   ├── SafeBlendAllocationStrategy (+ _SafeBlendMixin)
│   ├── VXUSSubstitutionStrategy (+ _VXUSSubstitutionMixin)
│   ├── SingleProductAllocationStrategy
│   │   ├── KodexNasdaqAllocationStrategy
│   │   ├── TimeNasdaqAllocationStrategy
│   │   └── KoActNasdaqAllocationStrategy
│   └── NasdaqProductMixAllocationStrategy
├── DownsideTrendOverlayStrategy
├── _StaticRegimeBandStrategy
│   ├── STATIC_70_BND10_BIL10_GLD10
│   └── STATIC_RETIREMENT_7030
├── BASIC_BANG_DIV
├── RETIREMENT_7030_BAND
├── ASYMMETRIC_TREND_BAND
├── ASYMMETRIC_TREND_BAND_ADD_DEFENSE
└── ASYMMETRIC_TREND_BAND_ADD_DEFENSE2
    └── ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED
```

`_MarketRegimeObserver`는 `_StaticRegimeBandStrategy`가 시장 상태를 기록할 때
사용하는 합성 객체이며 전략 클래스의 부모는 아닙니다. `AllocationState`는 상태를
표현하는 Enum입니다.

## 전체 성과

공통 조건은 다음과 같습니다.

- 신호 계산: 거래일 종가
- 체결: 다음 거래일 시가
- 수수료: 0.015%
- 슬리피지: 0.020%
- 가격: Yahoo Finance 조정주가
- 무위험수익률: 연 3%

기본 ETF 전략은 2012-01-03부터 2026-07-31까지 계산했습니다. 실제 상품 매핑
전략은 상품 상장일부터 시작하므로 기간이 다른 성과를 직접 비교해서는 안 됩니다.
수치는 현재 데이터로 계산한 회고적 결과이며 미래 성과를 의미하지 않습니다.

| 전략 | 직접 부모 | 기간 | CAGR | MDD | 상세 문서 |
|---|---|---:|---:|---:|---|
| `RetirementAllocationStrategy` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 14.47% | -23.29% | [상태와 전이](strategies/retirement-allocation.md#retirementallocationstrategy) |
| `SafeBlendAllocationStrategy` | `RetirementAllocationStrategy` | 2012-01-03 ~ 2026-07-31 | 14.46% | -22.80% | [부모와의 차이](strategies/retirement-allocation.md#safeblendallocationstrategy) |
| `VXUSSubstitutionStrategy` | `RetirementAllocationStrategy` | 2012-01-03 ~ 2026-07-31 | 14.91% | -23.18% | [부모와의 차이](strategies/retirement-allocation.md#vxussubstitutionstrategy) |
| `SingleProductAllocationStrategy` | `RetirementAllocationStrategy` | 공통 상품 매핑 클래스 | — | — | [매핑 방식](strategies/retirement-allocation.md#singleproductallocationstrategy) |
| `KodexNasdaqAllocationStrategy` | `SingleProductAllocationStrategy` | 2021-04-09 ~ 2026-07-31 | 16.08% | -18.32% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| `TimeNasdaqAllocationStrategy` | `SingleProductAllocationStrategy` | 2022-05-11 ~ 2026-07-31 | 31.52% | -25.48% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| `KoActNasdaqAllocationStrategy` | `SingleProductAllocationStrategy` | 2025-02-25 ~ 2026-07-31 | 45.56% | -24.11% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| `NasdaqProductMixAllocationStrategy` | `RetirementAllocationStrategy` | 2025-02-25 ~ 2026-07-31 | 29.27% | -18.77% | [상품 차이](strategies/retirement-allocation.md#nasdaqproductmixallocationstrategy) |
| `DownsideTrendOverlayStrategy` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 13.99% | -25.83% | [배분 로직](strategies/downside-trend-overlay.md) |
| `STATIC_70_BND10_BIL10_GLD10` | `_StaticRegimeBandStrategy` | 2012-01-03 ~ 2026-07-31 | 15.28% | -27.57% | [고정 배분](strategies/allocation-band-strategies.md#static_70_bnd10_bil10_gld10) |
| `STATIC_RETIREMENT_7030` | `_StaticRegimeBandStrategy` | 2012-01-03 ~ 2026-07-31 | 14.63% | -27.31% | [고정 배분](strategies/allocation-band-strategies.md#static_retirement_7030) |
| `BASIC_BANG_DIV` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 10.91% | -24.15% | [상태와 전이](strategies/allocation-band-strategies.md#basic_bang_div) |
| `RETIREMENT_7030_BAND` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 14.46% | -29.67% | [밴드 규칙](strategies/allocation-band-strategies.md#retirement_7030_band) |
| `ASYMMETRIC_TREND_BAND` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 22.54% | -41.16% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band) |
| `ASYMMETRIC_TREND_BAND_ADD_DEFENSE` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 14.03% | -29.59% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense) |
| `ASYMMETRIC_TREND_BAND_ADD_DEFENSE2` | `BaseStrategy` | 2012-01-03 ~ 2026-07-31 | 14.22% | -29.48% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense2) |
| `ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED` | `ASYMMETRIC_TREND_BAND_ADD_DEFENSE2` | 2012-01-03 ~ 2026-07-31 | 14.60% | -25.32% | [부모와의 차이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense2_tuned) |

## 상세 문서

- [RetirementAllocationStrategy와 상품 매핑](strategies/retirement-allocation.md)
- [비대칭 추세 밴드와 방어 전략](strategies/asymmetric-trend-band.md)
- [고정 배분, RSI 및 단순 밴드 전략](strategies/allocation-band-strategies.md)
- [DownsideTrendOverlayStrategy](strategies/downside-trend-overlay.md)

## 전략 추가 시 문서화 항목

새 전략을 추가하면 이 문서의 상속 트리와 통합 성과표에 클래스를 추가합니다.
상세 문서에는 다음 내용을 기록합니다.

1. 직접 부모 클래스와 부모의 `evaluate()` 재사용 여부
2. 기준 자산과 실제 매매 상품의 매핑
3. 상태별 목표 비중과 인정되는 안전자산 비중
4. 각 상태에 진입하고 이탈하는 구체적인 조건과 확인 기간
5. 리밸런싱 밴드, 분할 실행 일수와 신호 사유
6. 부모 전략을 상속한 경우 부모 대비 변경점만 기술
7. 동일 조건의 성과와 구간·민감도 검증에서 확인된 한계

