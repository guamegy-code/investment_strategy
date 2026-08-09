# 전략 클래스

[← README](../README.md)

이 문서는 전략 클래스의 전체 관계와 동일한 백테스트 엔진으로 계산한 성과를 한
곳에서 보여줍니다. 자산배분 상태, 상태 전이와 리밸런싱 규칙은 전략 이름의 상세
문서 링크에서 확인할 수 있습니다.

## 전체 클래스 관계

아래 트리는 실제 Python 상속 관계를 기준으로 하되, 외부 import 호환만을 위해
남겨둔 래퍼 클래스는 표시하지 않습니다.

```text
BaseStrategy
├── RetirementAllocationLegacyStrategy
│   ├── RetirementAllocationStrategy
│   │   ├── RetirementAllocationProfitBandStrategy (+ _UpperRiskBandMixin + _DefensiveSafeBlendMixin)
│   │   │   └── RetirementAllocationProfitBandVXUSStrategy (+ _VXUSSubstitutionMixin)
│   │   │       ├── SingleProductAllocationStrategy
│   │   │       │   ├── KodexNasdaqAllocationStrategy
│   │   │       │   ├── TimeNasdaqAllocationStrategy
│   │   │       │   └── KoActNasdaqAllocationStrategy
│   │   │       └── NasdaqProductMixAllocationStrategy
│   │   ├── RetirementAllocationSafeBlendStrategy (+ _SafeBlendMixin)
│   │   └── RetirementAllocationVXUSStrategy (+ _DefensiveSafeBlendMixin + _VXUSSubstitutionMixin)
│   │       └── RetirementAllocationSPYStrategy
│   ├── SafeBlendAllocationStrategy (+ _DefensiveSafeBlendMixin)
│   └── VXUSSubstitutionStrategy (+ _VXUSSubstitutionMixin)
├── DownsideTrendOverlayStrategy
├── EXPANDING_RISK_FORECAST_30_70
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

## Mixin 역할

Mixin은 단독 실행 전략이 아니라 부모 전략의 목표 계산이나 주문 판단 중 한 부분만
변경하는 재사용 클래스입니다. 따라서 별도 성과를 갖지 않으며, 실제 영향은 Mixin을
조합한 전략의 성과로 확인합니다.

| Mixin | 변경하는 동작 | 목적과 트레이드오프 |
|---|---|---|
| `_SafeBlendMixin` | 모든 상태에서 BND/BIL ROC40 차이를 25% 단위 혼합 비중으로 변환 | 단일 안전자산 교체의 시점 위험을 줄이지만 목표 변화와 거래가 늘 수 있음 |
| `_DefensiveSafeBlendMixin` | `_SafeBlendMixin`의 혼합 목표를 `BEAR`·`RECOVERY`에만 적용 | 상승 국면의 기존 동작을 보존하지만 BULL/CAUTION에서는 혼합 분산 효과가 없음 |
| `_VXUSSubstitutionMixin` | 부모가 계산한 BND 중 위험 한도 여유분만 VXUS로 대체하고 BIL은 유지 | 해외주식 분산과 기대수익 기회를 추가하지만 주식 동반 하락과 추가 거래 위험이 생김 |
| `_UpperRiskBandMixin` | BULL/CAUTION에서 QQQ 실제 비중 70~80%를 유지하고 80% 도달 시 70%로 복원 | 상승 수익을 오래 보유하지만 위험자산 집중과 MDD가 커질 수 있음 |

`_VXUSSubstitutionMixin`의 대체 자산 상수를 SPY로 바꾸면
`RetirementAllocationSPYStrategy`가 됩니다. 계산식과 Mixin 조합 순서는
[퇴직연금 전략의 공통 Mixin](strategies/retirement-allocation.md#공통-mixin)에
정리되어 있습니다.

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

| 전략 | 기간 | CAGR | MDD | 상세 문서 |
|---|---:|---:|---:|---|
| <small><code>BaseStrategy</code></small><br>└ <code>RetirementAllocationLegacyStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.47% | -23.29% | [과거 상태와 전이](strategies/retirement-allocation.md#retirementallocationlegacystrategy) |
| <small><code>RetirementAllocationLegacyStrategy</code></small><br>└ <code>RetirementAllocationStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.55% | -23.26% | [기본 선택적 주문 생략](strategies/retirement-allocation.md#retirementallocationstrategy) |
| <small><code>RetirementAllocationStrategy + _UpperRiskBandMixin + _DefensiveSafeBlendMixin</code></small><br>└ <code>RetirementAllocationProfitBandStrategy</code> | 2012-01-03 ~ 2026-07-31 | 15.15% | -23.64% | [ProfitBand와 방어 혼합](strategies/retirement-allocation.md#retirementallocationprofitbandstrategy) |
| <small><code>RetirementAllocationProfitBandStrategy + _VXUSSubstitutionMixin</code></small><br>└ <code>RetirementAllocationProfitBandVXUSStrategy</code> | 2012-01-03 ~ 2026-07-31 | 15.63% | -23.20% | [ProfitBand와 VXUS](strategies/retirement-allocation.md#retirementallocationprofitbandvxusstrategy) |
| <small><code>RetirementAllocationStrategy + _SafeBlendMixin</code></small><br>└ <code>RetirementAllocationSafeBlendStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.53% | -22.76% | [SafeBlend](strategies/retirement-allocation.md#retirementallocationsafeblendstrategy) |
| <small><code>RetirementAllocationStrategy + _DefensiveSafeBlendMixin + _VXUSSubstitutionMixin</code></small><br>└ <code>RetirementAllocationVXUSStrategy</code> | 2012-01-03 ~ 2026-07-31 | 15.05% | -22.42% | [방어 국면 SafeBlend와 VXUS](strategies/retirement-allocation.md#retirementallocationvxusstrategy) |
| <small><code>RetirementAllocationVXUSStrategy</code></small><br>└ <code>RetirementAllocationSPYStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.90% | -22.60% | [방어 국면 SafeBlend와 SPY](strategies/retirement-allocation.md#retirementallocationspystrategy) |
| <small><code>RetirementAllocationLegacyStrategy</code></small><br>└ <code>SafeBlendAllocationStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.50% | -22.90% | [방어 국면 SafeBlend](strategies/retirement-allocation.md#safeblendallocationstrategy) |
| <small><code>RetirementAllocationLegacyStrategy</code></small><br>└ <code>VXUSSubstitutionStrategy</code> | 2012-01-03 ~ 2026-07-31 | 14.91% | -23.18% | [부모와의 차이](strategies/retirement-allocation.md#vxussubstitutionstrategy) |
| <small><code>RetirementAllocationProfitBandVXUSStrategy</code></small><br>└ <code>SingleProductAllocationStrategy</code> | 공통 상품 매핑 클래스 | — | — | [매핑 방식](strategies/retirement-allocation.md#singleproductallocationstrategy) |
| <small><code>SingleProductAllocationStrategy</code></small><br>└ <code>KodexNasdaqAllocationStrategy</code> | 2021-04-09 ~ 2026-07-31 | 17.75% | -18.18% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| <small><code>SingleProductAllocationStrategy</code></small><br>└ <code>TimeNasdaqAllocationStrategy</code> | 2022-05-11 ~ 2026-07-31 | 34.07% | -27.23% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| <small><code>SingleProductAllocationStrategy</code></small><br>└ <code>KoActNasdaqAllocationStrategy</code> | 2025-02-25 ~ 2026-07-31 | 46.31% | -26.11% | [상품 차이](strategies/retirement-allocation.md#실제-상품-매핑-전략) |
| <small><code>RetirementAllocationProfitBandVXUSStrategy</code></small><br>└ <code>NasdaqProductMixAllocationStrategy</code> | 2025-02-25 ~ 2026-07-31 | 31.44% | -18.71% | [상품 차이](strategies/retirement-allocation.md#nasdaqproductmixallocationstrategy) |
| <small><code>BaseStrategy</code></small><br>└ <code>DownsideTrendOverlayStrategy</code> | 2012-01-03 ~ 2026-07-31 | 13.99% | -25.83% | [배분 로직](strategies/downside-trend-overlay.md) |
| <small><code>BaseStrategy</code></small><br>└ <code>EXPANDING_RISK_FORECAST_30_70</code> | 2012-01-03 ~ 2026-07-31 | 12.67% | -22.33% | [예측·배분 로직](strategies/expanding-risk-forecast.md) |
| <small><code>_StaticRegimeBandStrategy</code></small><br>└ <code>STATIC_70_BND10_BIL10_GLD10</code> | 2012-01-03 ~ 2026-07-31 | 15.28% | -27.57% | [고정 배분](strategies/allocation-band-strategies.md#static_70_bnd10_bil10_gld10) |
| <small><code>_StaticRegimeBandStrategy</code></small><br>└ <code>STATIC_RETIREMENT_7030</code> | 2012-01-03 ~ 2026-07-31 | 14.63% | -27.31% | [고정 배분](strategies/allocation-band-strategies.md#static_retirement_7030) |
| <small><code>BaseStrategy</code></small><br>└ <code>BASIC_BANG_DIV</code> | 2012-01-03 ~ 2026-07-31 | 10.91% | -24.15% | [상태와 전이](strategies/allocation-band-strategies.md#basic_bang_div) |
| <small><code>BaseStrategy</code></small><br>└ <code>RETIREMENT_7030_BAND</code> | 2012-01-03 ~ 2026-07-31 | 14.46% | -29.67% | [밴드 규칙](strategies/allocation-band-strategies.md#retirement_7030_band) |
| <small><code>BaseStrategy</code></small><br>└ <code>ASYMMETRIC_TREND_BAND</code> | 2012-01-03 ~ 2026-07-31 | 22.54% | -41.16% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band) |
| <small><code>BaseStrategy</code></small><br>└ <code>ASYMMETRIC_TREND_BAND_ADD_DEFENSE</code> | 2012-01-03 ~ 2026-07-31 | 14.03% | -29.59% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense) |
| <small><code>BaseStrategy</code></small><br>└ <code>ASYMMETRIC_TREND_BAND_ADD_DEFENSE2</code> | 2012-01-03 ~ 2026-07-31 | 14.22% | -29.48% | [상태와 전이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense2) |
| <small><code>ASYMMETRIC_TREND_BAND_ADD_DEFENSE2</code></small><br>└ <code>ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED</code> | 2012-01-03 ~ 2026-07-31 | 14.60% | -25.32% | [부모와의 차이](strategies/asymmetric-trend-band.md#asymmetric_trend_band_add_defense2_tuned) |

## 상세 문서

- [RetirementAllocationStrategy와 상품 매핑](strategies/retirement-allocation.md)
- [비대칭 추세 밴드와 방어 전략](strategies/asymmetric-trend-band.md)
- [고정 배분, RSI 및 단순 밴드 전략](strategies/allocation-band-strategies.md)
- [DownsideTrendOverlayStrategy](strategies/downside-trend-overlay.md)
- [EXPANDING_RISK_FORECAST_30_70](strategies/expanding-risk-forecast.md)

## 전략 추가 시 문서화 항목

새 전략을 추가하면 이 문서의 상속 트리와 통합 성과표에 클래스를 추가합니다.
상세 문서에는 다음 내용을 기록합니다.

1. 직접 부모 클래스와 부모의 `evaluate()` 재사용 여부
2. 조합한 Mixin의 역할, 변경하는 동작과 적용 순서
3. 기준 자산과 실제 매매 상품의 매핑
4. 상태별 목표 비중과 인정되는 안전자산 비중
5. 각 상태에 진입하고 이탈하는 구체적인 조건과 확인 기간
6. 리밸런싱 밴드, 분할 실행 일수와 신호 사유
7. 부모 전략을 상속한 경우 부모 대비 변경점, 변경 목적, 그리고 수익률·위험·거래에
   미치는 영향과 트레이드오프를 기술
8. 동일 조건의 성과와 구간·민감도 검증에서 확인된 한계

부모와 파생 전략의 규칙이나 성과를 비교할 때는 표를 우선 사용합니다. 롤링 구간,
변경 이유와 수치만으로 드러나지 않는 트레이드오프는 표 아래 문장으로 설명합니다.
