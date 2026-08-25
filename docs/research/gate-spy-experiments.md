# Gate SPY 전략 실험

이 문서는 QQQ 약세를 SPY로 확인하는 BULL→CAUTION 게이트, 신용 확인,
사전 CAUTION 버퍼, TDF/BIL 안전자산 이탈·복귀와 실행 규칙 실험을 기록합니다.

## 연구 질문

1. QQQ 단독 약세보다 SPY 확인을 추가하면 잘못된 CAUTION 진입을 줄일 수
   있는가?
2. 신용시장이 SPY 확인을 보완하거나 대체할 수 있는가?
3. 안전자산을 단계적으로 이동하거나 이탈·복귀를 늦추면 성과가 개선되는가?
4. 실행 중복을 줄여 비용과 불필요한 거래를 낮출 수 있는가?

## SPY 확인 게이트

이전 Stress SPY 규칙을 QQQ 약세 후 SPY가 확인하는 2단계 규칙으로 바꾼
것이 가장 명확하게 남은 개선이었습니다.

| 후보 | CAGR | MDD | Calmar | 2021+ CAGR | 2021+ MDD |
|---|---:|---:|---:|---:|---:|
| 기존 Stress SPY | 15.09% | -24.48% | 0.617 | 15.01% | -24.48% |
| Gate 3 + SPY 약세, 확인 1일 | 15.58% | -23.48% | 0.664 | 15.55% | -23.48% |
| Gate 4 + SPY 약세, 확인 1일 | 15.61% | -23.66% | 0.660 | 15.56% | -23.66% |
| Gate 4 + SPY 급락 | 15.54% | -23.89% | 0.651 | 15.34% | -23.89% |
| Gate 4 + SPY 약세, 확인 2일 | 15.08% | -25.25% | 0.597 | 15.05% | -25.25% |

Gate 3은 Gate 4보다 CAGR이 0.03%p 낮았지만 MDD와 Calmar가 더 좋았습니다.

결정: QQQ EMA20 하회, risk-off 점수 3 이상, SPY EMA20 하회, SPY ROC5
-1% 이하, 확인 1일 규칙을 전략 12~14에 채택했습니다.

## 신용시장 확인

| 후보 | CAGR | MDD | 2021+ CAGR | 해석 |
|---|---:|---:|---:|---|
| Gate SPY | 15.58% | -23.48% | 15.55% | 비교 기준 |
| SPY 또는 신용 진입 | 15.62% | -23.48% | 15.55% | 최근 구간 변화 없음 |
| SPY 게이트 + 빠른 신용 BEAR | 15.09% | -23.48% | 15.55% | 과거 CAGR 악화 |
| 모든 상태 게이트 | 15.16% | -23.48% | 15.55% | 복잡성 대비 이점 없음 |

결정: `SPY 또는 신용`의 전체 CAGR 개선은 약 0.037%p에 불과하고 2021+
차이가 없어 채택하지 않습니다. 더 넓은 신용 사용은 CAGR을 낮췄습니다.

## 사전 CAUTION 버퍼

QQQ 비중 72.5%·75%와 risk-off 점수 2·3을 조합한 버퍼는 기준과 중복되거나
소폭 악화됐습니다. 가장 높은 후보도 CAGR 15.59%, MDD -23.57%로 Gate SPY
기준의 15.58%, -23.48%를 명확히 지배하지 못했습니다.

결정: 사전 버퍼를 추가하지 않습니다.

## 안전자산 이탈·복귀 확인

현재 규칙보다 이탈이나 복귀 확인을 늦추면 거래 횟수는 줄었지만 성과가
악화됐습니다. 예를 들어 이탈 점수 5·확인 2일은 CAGR 15.15%, MDD -23.69%,
점수 6·확인 2일은 CAGR 14.99%, MDD -24.57%였습니다. 점수 7은 안전자산
사건을 없애 CAGR은 높였지만 MDD가 -27.53%로 크게 악화됐습니다.

결정: 느린 이탈·복귀 확인을 채택하지 않습니다.

## 강한 CAUTION에서 TDF 일부 유지

| 후보 | CAGR | MDD | Calmar |
|---|---:|---:|---:|
| 현재 Gate SPY | 15.58% | -23.48% | 0.664 |
| TDF 20% 유지 | 15.67% | -24.33% | 0.644 |
| TDF 40% 유지 | 15.68% | -25.40% | 0.617 |
| TDF 60% 유지 | 15.71% | -26.33% | 0.597 |

CAGR은 조금 높아졌지만 TDF 유지 비중이 커질수록 MDD가 악화됐습니다.

결정: 강한 CAUTION의 부분 TDF 유지를 기각합니다.

## TDF 전용 이탈 필터

SPY·신용·TDF 자체 추세를 이용한 이탈 필터는 모두 기준 CAGR보다 낮았습니다.
특히 신용 약세 필터는 MDD를 -27.62%로 악화시켰습니다.

결정: TDF 전용 이탈 필터를 추가하지 않습니다.

## 단계형 안전자산

기준 이진 100/0 규칙은 CAGR 15.95%, MDD -23.82%, Calmar 0.670이었습니다.
점수 5에서 TDF 20~80%를 유지하고 점수 6에서 0%로 만드는 단계형 후보는
모두 CAGR과 MDD가 기준보다 나빴습니다.

결정: 이진 TDF 100%/0% 구조를 유지합니다.

## 실행 규칙

동일한 리밸런싱 의도를 중복 제거한 `Current 1/1 + dedup`은 CAGR 15.95%,
MDD -23.82%로 현재 1/1보다 소폭 개선됐고 중복 주문 2개를 억제했습니다.
반면 안전자산 복귀를 2·3·5일로 늦추면 CAGR과 MDD가 모두 악화됐습니다.

결정: 중복 제거는 실행 정리로 유지하고 복잡한 이탈·복귀 지연은 채택하지
않습니다.

## 최종 결론

- SPY는 QQQ 약세의 유효한 2단계 확인으로 채택했습니다.
- 신용 확인과 사전 버퍼는 최근 구간에서 추가 정보를 주지 못했습니다.
- 부분·단계형 TDF보다 단순한 이진 TDF/BIL 구조가 더 강했습니다.
- 현재 대표 구현은
  `14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml`입니다.

## 재현 코드

- `validation/conditional_observation_gate.py`
- `validation/state_conditional_observation_gates.py`
- `validation/pre_caution_buffer.py`
- `validation/gate_spy_safe_sleeve_hysteresis.py`
- `validation/gate_spy_partial_safe_sleeve.py`
- `validation/gate_spy_safe_sleeve_confirmation.py`
- `validation/gate_spy_execution_improvements.py`
- `validation/gate_spy_tdf_exit_filter.py`
- `validation/gate_spy_rebalance_significance.py`
- `validation/gate_spy_tiered_safe_sleeve.py`

