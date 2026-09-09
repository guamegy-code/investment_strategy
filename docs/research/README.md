# 전략 연구 기록

이 디렉터리는 현재 전략 YAML만으로는 알 수 없는 연구 결정의 근거를
보관합니다. `results/` 아래의 생성 보고서는 다시 만들 수 있으며 Git에서
제외합니다. 여기에는 기각된 아이디어를 모르고 반복하지 않도록 연구 질문,
대표 근거와 최종 결론만 남깁니다.

## 상태 구분

| 상태 | 의미 |
|---|---|
| 채택 | 현재 전략이나 실행 규칙에 반영된 결정 |
| 관찰 | 조건을 고정해 향후 데이터로 관찰하지만 생산 전략에는 반영하지 않은 후보 |
| 기각 | 목표나 강건성 기준을 통과하지 못한 후보 |
| 탐색 | 결과는 남아 있지만 신뢰할 만한 과거 승격 결정이 확인되지 않은 실험 |
| 차단 | 시점정보가 보존된 데이터 등 필수 전제조건을 확보하지 못한 실험 |

## 연구 문서

| 날짜 | 주제 | 주요 결론 | 생산 전략 영향 | 문서 |
|---|---|---|---|---|
| 2026-08-02~2026-08-04 | 상태 전이와 방어적 배분 | 빠른 BEAR·연속형·다중시장 대체는 기각, 엄격한 회복은 관찰 | 없음, 추세 밴드 전략은 비활성 | [상태 전이와 방어적 배분 실험](state-transition-defense-experiments.md) |
| 2026-08-02 | 위험자산 대체 | VXUS는 별도 전략에서만 사용하고 보편적 방어 자산으로 확대하지 않음 | VXUS 전략 계열에만 반영 | [위험자산 대체 실험](risk-asset-substitution-experiments.md) |
| 2026-08-08~2026-08-09 | 확률·경고 모델 | 포트폴리오·정밀도·hazard·연속 위험 후보가 후기 승격 기준에 실패 | 없음 | [확률·경고 모델 실험](probability-warning-experiments.md) |
| 2026-08-22 | TDF2050 프록시와 자산배분 | 프록시는 연구용으로 허용하고 생산 위험 비중과 단순 TDF/BIL 구조 유지 | TDF 전략 계열의 근거 | [TDF2050 프록시와 자산배분 실험](tdf2050-allocation-experiments.md) |
| 2026-08-24 | Gate SPY | QQQ 약세 후 SPY 확인을 채택하고 추가 신용·버퍼·단계형 안전자산은 기각 | 전략 12~14에 반영 | [Gate SPY 전략 실험](gate-spy-experiments.md) |
| 2026-08-25 | VXN, 신용, CNN Fear & Greed, 극단공포 회복 | 시험한 시장 상황 지표를 모두 기각하거나 데이터 부족으로 중단 | 없음 | [시장 상황 지표 실험](market-condition-signal-experiments.md) |
| 2026-08-26 | 상태 조건부 연속 배분 PoC | CAUTION 최대 5%p 조정은 일관된 소폭 위험 감소와 소폭 수익 희생, 10%p는 기각 | 생산 변경 없음, 5%p는 위험 예산형 그림자 후보 | [상태 조건부 연속 배분 PoC](state-conditioned-continuous-overlay-poc.md) |
| 2026-08-26 | 상태/연속 전략 앙상블 PoC | 연속 10% 혼합은 작은 위험 개선과 더 작은 수익 희생, 20%는 기각 | 생산 변경 없음, 10%는 효율적 그림자 후보 | [상태/연속 전략 앙상블 PoC](state-continuous-ensemble-poc.md) |
| 2026-08-28 | 네 상태 정보 충족성 PoC | 연속 정보는 수익률 예측에 일부 증분이 있으나, 하방위험 개선은 두 OOS 구간에서 재현되지 않음 | 생산 변경 없음 | [네 상태 정보 충족성 PoC](state-sufficiency-poc.md) |
| 2026-08-28 | 15번 사전 BEAR 방어와 리밸런싱 | 사전 경보 BIL 100%, 확정 BEAR TDF 20%, 7.5%p 허용폭을 채택 | 전략 15 버전 2에 반영 | [15번 사전 BEAR 방어 및 리밸런싱 개선](band-7030-tdf-state-bil-experiments.md) |
| 2026-08-29 | 엄격한 DEFENSIVE_CAUTION 5%p 감축 PoC | 동일한 2012-01-03 이후 구간에서도 전략 14·15 모두 승격 실패, 전략 15에서는 CAGR -0.661%p와 리밸런싱 27→135회의 대가가 발생 | 생산 변경 없음 | [엄격한 DEFENSIVE_CAUTION 5%p 감축 PoC](defensive-caution-overlay-poc.md) |
| 2026-08-29 | 상태 조건부 크로스애셋 로테이션 PoC | 원화 기준 CAGR은 +0.510%p였지만 Strategy 15 대체 기준에는 미달 | Strategy 15 유지, 별도 Strategy 16 자동 로테이션으로 운용·관찰 | [상태 조건부 크로스애셋 로테이션 PoC](state-conditioned-cross-asset-rotation-poc.md) |
| 2026-08-30 | 전략 15 경로 보존형 GLD 오버레이 PoC | 전체 목표 재설정 경로를 제거해도 조건부 GLD는 CAGR -0.115%p, MDD +0.484%p의 위험·수익 교환 | 생산 변경 없음, 격리형 후보만 관찰 | [전략 15 경로 보존형 GLD 오버레이 PoC](strategy15-isolated-gold-overlay-poc.md) |
| 2026-08-30 | 다중자산 추세추종 독립 슬리브 PoC | 합성 롱/숏은 독립성 기준을 통과했지만 5%·10% 대체 결합에서 전략 15 CAGR이 -0.695%p·-1.391%p 하락; 롱/현금 구현형도 승격 실패 | 생산 변경 없음, 자본효율형 원형 데이터 확보 여부를 다음 단계로 검토 | [다중자산 추세추종 독립 슬리브 PoC](multi-asset-trend-sleeve-poc.md) |
| 2026-08-30 | 위험자산 70% 횡단면 모멘텀 PoC | 상위 3개 선택은 정적 분산보다 CAGR +1.824%p였지만 전략 15보다 -4.167%p, MDD -1.975%p 악화 | 생산 변경 없음, 상위 개수·기간 튜닝 없이 종료 | [위험자산 70% 횡단면 모멘텀 PoC](cross-sectional-risk-momentum-poc.md) |
| 2026-09-03 | Buy 3 Dip 파라미터 최적화 | 10%/18%/32% 진입점과 QQQ 87%/90%/100%가 전체·개발·최근 구간에서 CAGR과 MDD를 함께 개선 | 원본 19번을 유지하고 별도 20번 전략으로 반영 | [Buy 3 Dip 파라미터 최적화](buy-3dip-parameter-optimization.md) |
| 2026-09-03 | Buy 3 Dip stage 4 추가 | 7,680개 후보에서 기존 20번의 CAGR·MDD를 동시에 개선한 stage 0~4 구조가 없음 | 전략 20 변경 없음 | [Buy 3 Dip stage 4 추가 실험](buy-3dip-stage4-experiment.md) |
| 2026-09-03 | Buy 3 Dip 진입·회복 공동 최적화 | 진입 -10%/-20%/-32.5%, 회복 +7.5%/-8.5%/-17.5%가 CAGR·MDD·Calmar를 함께 개선 | 20번 유지, 별도 21번 전략으로 생성 | [Buy 3 Dip 진입·회복 시점 공동 최적화](buy-3dip-joint-threshold-optimization.md) |
| 2026-09-03 | Buy 3 Dip 회복 지연 비중 탐색 | stage별 QQQ 비중 재탐색에서 21번의 87%/90%/100%를 CAGR·MDD 동시 개선한 후보 없음 | 21번 유지, 88%/90%/100%는 CAGR 절충 후보로 기록 | [Buy 3 Dip 회복 지연 비중 탐색](buy-3dip-delayed-recovery-allocation-optimization.md) |
| 2026-09-03 | Buy 3 Dip 분할 현금화 | stage 1~3의 회복 매도를 두 번으로 나눈 후보는 21번의 CAGR·MDD를 함께 개선하지 못함 | 21번의 즉시 한 단계 현금화 유지 | [Buy 3 Dip 분할 현금화 실험](buy-3dip-partial-unwind-experiment.md) |
| 2026-09-04 | Buy 3 Dip 동적 진입 조건 | 변동성 비율·급락 속도 기반 후보는 CAGR과 MDD가 모두 악화 | 21번 유지 | [Buy 3 Dip 동적 진입 조건 실험](buy-3dip-dynamic-threshold-experiments.md) |
| 2026-09-04 | Buy 3 Dip 파라미터 안정성 | 인접 729개 조합에 21번을 CAGR·MDD 모두 이긴 후보 없음, 근방은 완만 | 21번 유지 | [Buy 3 Dip 파라미터 안정성 검증](buy-3dip-parameter-stability.md) |
| 2026-09-04 | Buy 3 Dip 밸류에이션 프록시 | S&P 500 CAPE 프록시에서 stage 0/1 축소 후보가 전 구간·분할 구간 모두 개선 | Nasdaq-100 직접 밸류에이션 데이터 검증 전까지 연구 후보 | [Buy 3 Dip 밸류에이션 프록시 실험](buy-3dip-valuation-proxy-experiment.md) |
| 2026-09-07 | Buy 3 Dip 합성 밸류에이션 및 추가 튜닝 | 네 지표 합성 후 stage 0 고평가 방어를 강화하고 stage 1 감축을 줄여 전체·개발·최근 CAGR을 추가 개선 | 21번 유지, 22번 버전 2에 반영 | [Buy 3 Dip 합성 밸류에이션 최적화](buy-3dip-composite-valuation-optimization.md) |
| 2026-09-09 | QQQ 구조적 방어와 밸류에이션 급락 방어 | 23B는 추세 방어 기준으로 유지하고, 합성점수 고점 대비 25점 하락과 가격 약세 확인을 결합한 후보를 제한적 표본 위험을 명시해 24번으로 승격 | 전략 23·24 문서화, 24번 신규 반영 | [QQQ 구조적 방어와 밸류에이션 급락 방어 검증](qqq-structural-valuation-defense-validation.md) |
| 2026-09-09 | QQQ 밸류에이션 방어 타이밍 균형형 | 월간 밸류에이션은 유지하되 6점 극단 약세 즉시 진입, QQQ 30% 방어, 2일 회복을 결합한 중간 강도 후보가 전체·개발·최근 CAGR과 MDD를 함께 개선 | 24번 유지, 별도 전략 25 신규 반영 | [QQQ 밸류에이션 방어 타이밍 균형형 검증](qqq-valuation-defense-timing-balanced.md) |

## 관리 원칙

독립적인 연구 주제마다 문서 하나를 만듭니다. 대표 지표, 데이터 분할, 결정과
재현 코드 위치만 보존합니다. 원시 다운로드, 전체 history/trades, 모든
그리드 행은 커밋하지 않습니다. 이전 결정을 다시 검토할 때에는 기존 문서를
덮어쓰지 않고 해당 주제 문서에 날짜가 붙은 후속 절을 추가하거나 새 후속
문서를 만든 뒤 서로 연결합니다.
