# 30번 신용지표 검토 통합 기록

28번 연구 래퍼와 운영 30번을 구분해 HY OAS, BAA10Y 변화 속도, 지표 교체·결합을 기록한다. 운영 30번의 검증을 우선한다. 마지막의 인계 원문은 연구 착수 당시 지침을 보존한 기록이며, 이후 사용자의 별도 32번 채택 요청이 우선한다.


---

### HY OAS와 BAA10Y의 Strategy 28 연구 비교

검토일: 2026-09-29. 운영 전략, YAML, 배포 파일은 변경하지 않았다.

> **비교 범위 정정:** 이 절은 28번에서 파생한 연구용 래퍼의 진입 조건을 비교한다. 최근 구간의 BAA10Y 수치가 튜닝 전 30번 YAML과 같더라도, 이 래퍼는 운영 30번의 방어 비중·심화 방어·추가 매수 제한·해제 규칙을 재현하지 않는다. 30번을 정확한 기준선으로 재검증한 결과는 이 문서의 마지막 **운영 30번 기준 HY OAS 재검토** 절을 우선 참조한다.

#### 결론

HY OAS는 **시장 현황을 설명하는 별도 신용 경보**로 유용할 가능성이 있다. 이 연구용 규칙에서 BAA10Y를 대체하거나 두 지표를 함께 요구하는 진입 규칙은 지지되지 않는다. 닷컴 구간의 개선은 뚜렷했지만 금융위기 구간은 동일했고, 2012~2026 구간에서는 BAA10Y가 약간 나았다. 2020·2025 급락에서 HY OAS 단독 경보는 먼저 발생했지만 Strategy 28의 QQQ/SPY 상대약세 조건을 통과하지 못해 포트폴리오 결과는 바뀌지 않았다. 운영 30번에 대한 판단은 위 후속 검토에 기록한다.

#### 데이터와 재현

- 지표: [FRED `BAMLH0A0HYM2`](https://fred.stlouisfed.org/series/BAMLH0A0HYM2), 단위는 퍼센트포인트. FRED는 2026년 4월부터 공개 관측값을 최근 3년으로 제한한다고 명시한다.
- 안내 문서의 Wayback URL은 이 환경에서 차단됐다. 장기 입력은 [과거 FRED 형식 CSV의 외부 보관본](https://github.com/mhidper/talvi/blob/dd7561088360f3b8d7890d0e728c1d10c916399d/data/indicadores/BAMLH0A0HYM2.csv)을 `tmp/BAMLH0A0HYM2_historical.csv`에만 보관했다. 1996-12-31~2025-06-11의 숫자 관측값 7,426개다. 제3자 보관본이므로 원본성 한계가 있다.
- 기존 로컬 FRED 최근 CSV `tmp/BAMLH0A0HYM2.csv`는 2023-09-26~2026-09-24의 787개 관측값이다. 겹치는 448개 숫자 관측값은 전부 같았다. 별도 [오래된 보관본](https://github.com/csaladenes/eco-archive/blob/3e2674491969a6e14f7e0a707f8e23861724e033/BAMLH0A0HYM2.csv)과도 겹치는 6,322개 숫자 관측값이 전부 같았다. 결합 결과는 1996-12-31~2026-09-24의 날짜 중복 없는 7,765개 관측값이다. 중복 날짜에는 최근 CSV 값을 우선했다. 원시 ICE CSV는 Git의 `tmp/` 무시 규칙 밖으로 내보내지 않는다.
- 주식 종가 전에 신용값이 공개됐다고 가정하지 않는다. 먼저 QQQ 거래일에 전일 관측값을 채운 뒤 기본 1거래일 지연한다. 0·2거래일도 계산했다. 이는 실제 발표 시각을 복원한 검증은 아니다.
- 재현: `uv run python src/legacy-python/validation/strategy28_hy_oas_review.py` 및 `uv run python src/legacy-python/validation/strategy28_hy_oas_diagnostics.py`. 네 가지 핵심 결과는 `results/strategy28_hy_oas_{summary,events,transitions,rolling}.csv`, 독립 신용 경보는 `results/strategy28_hy_oas_credit_alerts.csv`에 있다. 실행 환경에서 실시간 FRED 접근이 막히면 로더는 위 로컬 최근 CSV를 사용하며 종료일을 경고한다.

#### 비교 설계

기존 Strategy 28 YAML을 읽되 연구용 `FailedDipOverlay`만 변형했다. 공통 조건은 최근 20거래일 QQQ/SPY 상대수익률 최저값 -5% 이하, 동일한 QQQ 65% 방어 상한, 동일한 가격·SPY 추세 해제다. 가격 단독 모델에는 기존 2단계·신저점 조건을 적용했다. 신용 모델은 해당 조건 대신 신용 경보를 사용한다.

| 모델 | 신용 진입 조건 |
| --- | --- |
| 가격만 | 신용 조건 없음 |
| BAA10Y | level ≥ 2.0%p, 20일 확대 ≥ 0.30%p |
| HY OAS | level ≥ 4.0%p, 20일 확대 ≥ 0.50%p |
| BAA + HY | 위 두 조건 모두 |
| HY Z252 | 252거래일 z-score ≥ 1.0 |

HY OAS의 level, 5일·20일 변화, 20일 평균, 252일 z-score, 60일 고점 대비 축소폭을 계산한다. 임계값은 탐색용 가정이며 최적화된 값이 아니다. 지연 1일의 HY level/change를 3.75/0.40 및 4.25/0.60으로 흔들고 비용 3배도 시험했다. 닷컴·금융위기는 기존 CAPE/BIL 대체 자료를 사용하므로 실제 당시 투자 가능 성과로 읽을 수 없다.

#### 기본 1거래일 지연 결과

수익률 수치는 CAGR, 손실 수치는 MDD다. 범위는 기존 연구의 닷컴 대체 구간(2000~2005), 금융위기 대체 구간(2007~2011), 최근 구간(2012~2026-07-31)이다.

| 구간 | 모델 | CAGR | MDD | Sharpe | Calmar | 방어 진입 수 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 닷컴 | 가격만 | -7.97% | -63.07% | -0.386 | -0.126 | 2 |
| 닷컴 | BAA10Y | -6.62% | -59.79% | -0.331 | -0.111 | 2 |
| 닷컴 | HY OAS | -5.66% | -57.35% | -0.300 | -0.099 | 2 |
| 닷컴 | BAA + HY | -7.91% | -62.92% | -0.379 | -0.126 | 2 |
| 금융위기 | BAA10Y / HY OAS / 결합 | 11.69% | -26.74% | 0.520 | 0.437 | 1 |
| 최근 | 가격만 | 21.14% | -16.35% | 1.049 | 1.293 | 1 |
| 최근 | BAA10Y | 21.19% | -16.25% | 1.053 | 1.304 | 1 |
| 최근 | HY OAS | 21.14% | -16.35% | 1.049 | 1.293 | 1 |
| 최근 | BAA + HY | 21.14% | -16.35% | 1.049 | 1.293 | 1 |

닷컴 두 번째 진입은 BAA10Y 2001-01-03, HY OAS 2000-10-06이었다. 첫 진입은 각각 2000-03-31과 2000-03-30, 최종 해제는 모두 2003-05-28이었다. 금융위기는 모두 2008-01-23 진입, 2009-07-28 해제였다. 최근 구간은 BAA10Y 2022-02-18, HY OAS 2022-05-03, 결합 2022-05-05 진입이었다. 모두 2023-04-12 해제됐다. 결합의 추가 확인은 유의미한 개선을 만들지 못했다.

#### 신용 경보와 매매 신호의 차이

상대약세 필터를 빼고 신용 조건만 본 1거래일 지연 최초 경보는 다음과 같다. 이는 매매 성과가 아니며, 개별 날짜가 곧 위기 예측 성공을 뜻하지 않는다.

| 사건 | BAA10Y | HY OAS |
| --- | --- | --- |
| 2018 Q4 | 2018-12-07 | 2018-11-16 |
| 코로나 | 2020-03-03 | 2020-02-28 |
| 2022 약세장 | 2022-02-18 | 2022-03-11 |
| 2025 급락 | 2025-04-08 | 2025-04-04 |

HY OAS가 2018·2020·2025에는 먼저 신용 스트레스를 보여줬지만 2022에는 늦었다. 2020·2025에는 기존 상대약세 필터가 두 신용 모델의 방어 진입을 막았다. 신용만으로 방어하도록 바꾸면 정상 조정의 불필요한 신호도 함께 늘 수 있다. 예를 들어 탐색 기준에서 2019-05~12에 HY OAS 신용 경보가 11거래일, BAA10Y는 1거래일이었다. 이를 오경보로 확정하려면 사전에 정의한 사건 라벨과 독립 검증이 필요하다.

#### 견고성 및 한계

- 닷컴 HY OAS의 0/1/2일 지연 CAGR은 -6.80/-5.66/-5.74%, MDD는 -60.26/-57.35/-57.55%였다. 1·2일 결론은 비슷하지만 0일 신호는 낙관적 결과로 취급하지 않는다.
- HY OAS 비용 3배에서 닷컴 CAGR/MDD는 -6.14%/-58.11%, 금융위기 11.45%/-27.34%, 최근 20.96%/-16.91%였다.
- 닷컴의 느슨한 3.75/0.40 기준은 -5.30%/-56.40%, 엄격한 4.25/0.60 기준은 -5.92%/-58.02%였다. 최근 구간에서는 느슨한 기준의 CAGR이 20.83%로 낮아지고 진입이 2회로 늘었다. 임계값 선택에 따라 진입 수와 성과가 바뀐다.
- 최근 구간의 연말 종료 3년 창 12개, 5년 창 10개에서 HY OAS 단독 모델은 BAA10Y보다 CAGR이 높은 창이 **0개**였다. MDD가 좋은 창은 각각 2개와 1개였다. 닷컴 구간의 3년 창은 3개뿐이고 금융위기 3년 창은 2개뿐이라 장기 반복성 근거가 약하다.
- 가격 이력상 QQQ가 1999년부터라 1998 LTCM은 Strategy 28 포트폴리오 백테스트에 포함할 수 없다. 사전 구간과 OOS도 독립 설계가 아니다. 과거 위기와 탐색 임계값을 보며 판단한 결과를 운영 규칙으로 쓰면 과최적화 위험이 크다.
- 신용지표는 관측일과 실제 공개 시각이 다를 수 있다. FRED의 사후 수정값과 제3자 보관본의 provenance도 point-in-time 검증을 대신하지 못한다.

현재 권고는 HY OAS level·5/20일 변화·z-score를 BAA10Y와 나란히 **연구용 현황 지표**로 유지하고, 운영 판단에 연결하기 전 별도 사전등록 기준과 미관측 기간 검증을 하는 것이다. HYG/JNK 가격은 OAS 대용으로 취급하지 않는다.


---

### 30번 전략의 신용 스프레드 변화 속도 점검 (2026-09-29)

#### 현재 규칙

30번은 BAA10Y의 `Close` 시계열에서 **수준과 변화량을 모두** 계산한다. 신용 방어 진입의 신용 조건은 `BAA10Y.close >= 2.0`%p와 `credit_change20 >= 0.30`%p다. `credit_change20 = BAA10Y.close - BAA10Y.close / (1 + BAA10Y.roc20 / 100)`이므로, 정상적인 양의 스프레드에서는 현재 값과 20 QQQ 거래일 전 값의 차이다. 실제 방어 진입에는 별도로 QQQ의 SPY 대비 최근 상대 약세 조건도 적용된다. 방어 해제에는 BAA10Y 수준과 가격·상대강도 회복 및 확인 기간, 심화 방어 해제에는 고점 대비 0.50%p 축소도 사용한다.

따라서 `Close`는 입력 데이터 필드이고, 전략이 절대 수준만 본다는 뜻은 아니다. 다만 5일 등 더 짧은 변화 속도나 과거 변동성 대비 이례적인 변화는 현재 진입 조건에 없다.

#### 별도 관찰 실험

`strategy30_credit_change_review.py`는 기존 규칙을 수정하지 않고 BAA10Y와 HY OAS에 대해 5·20 QQQ 거래일 차이, 직전 252거래일의 같은 길이 변화량을 기준으로 한 z 점수를 계산했다. z 점수 계산에는 해당 날짜 이전 값만 썼고 지표는 1 QQQ 거래일 늦춰 정렬했다. `z >= 2`는 빠른 확대, `z <= -2`는 빠른 축소의 **설명용** 기준이다. 이는 전략 성과를 검증한 결과가 아니며, 신용 조건만 만족한 첫 날짜는 실제 전략 주문일과 다르다.

| 관찰 구간 | BAA 5일 이례 확대 | HY 5일 이례 확대 | 기존 BAA 진입 신용 조건 | 기존 검토의 HY 진입 신용 조건 |
| --- | --- | --- | --- | --- |
| 2018년 4분기~2019년 4월 | 2018-10-25 | 2018-10-11 | 2018-12-07 | 2018-11-16 |
| 코로나 충격 | 2020-02-26 | 2020-02-25 | 2020-03-03 | 2020-02-28 |
| 2021년 11월~2023년 1월 | 2021-11-29 | 2021-11-29 | 2022-02-18 | 2022-03-11 |
| 2025년 2~8월 | 2025-03-04 | 2025-03-13 | 2025-04-08 | 2025-04-04 |

조기 포착 가능성은 있지만 이례적 변화가 반드시 큰 주가 하락으로 연결되는 것은 아니다. 참고 구간인 2024년에도 BAA 5일 이례 확대가 6거래일, HY가 6거래일 발생했고, 기존 BAA 진입 신용 조건은 한 번도 충족되지 않았다. 5일 신호를 단독 주문 조건으로 바꾸면 거짓 경보가 늘 수 있다. 반대로 2022년 같은 긴 하락장에서 5일·20일 이례 확대는 기존 절대 수준 조건보다 수개월 앞서 관찰되었다. 단기 경보는 시장 현황 설명이나 추가 조사 신호로 유용할 수 있다.

신용 스프레드 확대·축소는 각각 신용 위험 악화·완화의 관찰값이다. 이를 QQQ의 즉각적인 급락·급등이나 향후 수익률로 동일시할 수 없다. 특히 `z <= -2`는 과거 대비 급격한 신용 완화를 뜻할 뿐 상승 매수 신호의 검증이 아니다. 단기 변화량을 운영 규칙에 넣으려면 사전에 정한 임계값으로 전체 전략 백테스트, 거짓 경보·거래 비용, 구간 밖 재현을 평가해야 한다.

재현: `uv run --offline python src/legacy-python/validation/strategy30_credit_change_review.py`. 세부 날짜와 각 구간의 신호 일수는 `results/strategy30_credit_change_lag1.csv`에 저장된다. 운영 YAML은 수정하지 않았다.


---

### 운영 30번 기준 HY OAS 재검토

2026-09-29. 당시 운영 전략과 YAML은 변경하지 않았다. 이 절은 앞의 **28번 연구 래퍼 비교**에서 사용한 기준선을 바로잡는다.

#### 무엇이 누락됐나

앞선 `strategy28_hy_oas_review.py`는 28번의 `FailedDipOverlay`를 변형했다. 이 래퍼의 BAA10Y 최근 구간 CAGR 21.188%는 튜닝 전 `YAML_30_BASE`와 같지만, 운영 30번을 검증한 결과가 아니다. 운영 30번은 1차 방어 QQQ 50%, 심화 방어 20%, 심화 방어 중 추가 매수 제한, BAA10Y < 3.0%p를 포함한 5일 해제 확인, 별도 deep-guard 해제를 사용한다. 이 차이 때문에 앞선 HY 대 BAA 비교를 30번의 성과 비교로 해석한 것은 잘못이다.

이번에는 `30_qqq_valuation_credit_guard_no_topup.yaml`을 그대로 읽은 `BASE30`과, 메모리에서 그 정의의 **신용 관련 식만** 바꾼 연구 변형을 비교했다. 목표 비중, 상태 우선순위, 추가 매수 제한, 가격·상대강도·가치평가 조건, 리밸런싱, 비용은 유지했다. `BASE30`의 원화 평가 CAGR 23.2306%, MDD -16.3029%는 기존 검증 문서의 23.231%/-16.303%를 재현했다.

#### 통제된 비교

모든 신용 관측값은 QQQ 거래일에 맞춘 뒤 기본 1거래일 지연했다. HY OAS 진입 가정은 level ≥ 4.0%p 및 20거래일 확대 ≥ 0.50%p다. 이 숫자는 사전 검증된 운영 임계값이 아니다.

| 변형 | 1차 진입 | 해제·심화 방어 |
| --- | --- | --- |
| BASE30 | 기존 BAA10Y | 기존 30번 그대로 |
| HY_ENTRY | HY OAS로 치환 | 기존 BAA10Y 유지 |
| BAA_OR_HY | 두 경보 중 하나 | 기존 BAA10Y 유지 |
| BAA_AND_HY | 두 경보 모두 | 기존 BAA10Y 유지 |
| FULL_HY | HY OAS로 치환 | HY OAS로 치환, 임시 해제값 4.5/4.0%p 및 고점 대비 0.75%p 축소 |

`HY_ENTRY`가 진입 정보의 추가 가치를 가장 명확하게 분리한다. `FULL_HY`는 해제 임계값까지 새로 정한 탐색 실험이므로 HY OAS 자체의 공정한 대체 성과로 해석하지 않는다.

##### 2012-01-03~2026-07-31 원화 평가

| 변형 | CAGR | MDD | Sharpe | Calmar | 거래 | 신용 경보 진입 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BASE30 | 23.231% | -16.303% | 1.004 | 1.425 | 59 | 2022-02-18 |
| HY_ENTRY | 23.144% | -16.303% | 0.999 | 1.420 | 61 | 2022-05-03 |
| BAA_OR_HY | 23.231% | -16.303% | 1.004 | 1.425 | 59 | 2022-02-18 |
| BAA_AND_HY | 23.144% | -16.303% | 0.999 | 1.420 | 61 | 2022-05-05 |
| FULL_HY | 23.144% | -16.303% | 0.999 | 1.420 | 61 | 2022-05-03 |

현재 구간에서 HY 경보는 BAA보다 늦어 2022년 사건 수익률과 MDD가 각각 -14.58%/-15.43%로, BASE30의 -13.70%/-14.56%보다 낮았다. `BAA_OR_HY`는 기준선과 거래·성과가 완전히 같아 추가 정보가 실제 주문으로 이어지지 않았다. 2020·2025에도 단독 HY 신용 경보가 먼저 나타났으나 기존 QQQ/SPY 상대약세 조건을 통과하지 못했고, 다섯 변형의 해당 사건 성과는 같았다.

##### 과거 위기 대체 구간, 달러 평가

| 구간 | BASE30 CAGR/MDD | HY_ENTRY CAGR/MDD | BAA_OR_HY CAGR/MDD | FULL_HY CAGR/MDD |
| --- | ---: | ---: | ---: | ---: |
| 닷컴 | 5.060% / -15.859% | 5.119% / -15.859% | 5.119% / -15.859% | 2.811% / -15.859% |
| 금융위기 | 10.994% / -25.509% | 10.994% / -25.509% | 10.994% / -25.509% | 7.665% / -25.509% |

닷컴에서는 HY가 두 번째 1차 경보를 2001-01-03에서 2000-10-06으로 앞당겼다. 그러나 심화 방어는 양쪽 모두 2000-04-03부터 켜져 789거래일 지속했다. 따라서 1차 경보의 앞당김이 실제 방어 손실 한도를 바꾸지 못했다. 금융위기는 모든 진입 전용 변형이 2008-01-23에 같은 경보를 냈다. `FULL_HY`의 CAGR 감소는 임시 HY 해제 임계값이 방어를 지나치게 오래 유지한 영향이 크며, 임계값을 최적화해 상쇄하면 과최적화 위험이 생긴다.

#### 민감도와 판단

- 최근 구간 원화 평가에서 0/1/2거래일 지연의 HY_ENTRY CAGR은 모두 23.144%였고 BASE30은 23.224/23.231/23.228%였다. 지연 2일에도 우열은 바뀌지 않았다.
- 비용 3배에서 최근 원화 CAGR은 BASE30 23.074%, HY_ENTRY 22.975%였다. 닷컴에서는 HY_ENTRY가 약 0.059%p 높았고 금융위기는 같았다.
- 최근 원화 평가의 연말 종료 3년 창 12개에서 HY_ENTRY가 BASE30보다 CAGR이 높은 창은 1개, 5년 창 10개에서는 0개였다. MDD가 개선된 창은 각각 4개였다. 평균 CAGR 차이는 -0.107%p, -0.133%p다.
- HY 진입을 느슨하게 3.75/0.40으로 두면 최근 원화가 아닌 달러 평가 CAGR이 21.112%로 내려가고 신용 경보가 2회가 됐다. 엄격한 4.25/0.60은 기본 HY_ENTRY와 같은 21.333%였다. 작은 임계값 변화에도 경보 수가 달라진다.

**판단:** 현 자료로는 HY OAS가 운영 30번의 BAA10Y 규칙을 개선한다는 근거가 부족하다. 시장 설명용 신용 지표로 BAA10Y 옆에 표시하는 연구는 가능하지만 주문·배분 규칙은 그대로 둔다. 다른 진입 조건이나 HY 고유의 해제 조건을 설계하려면 이 결과와 분리된 사전 지정 구간, 발표 시각 확인, 거짓 경보 비용을 포함한 검증이 필요하다. 닷컴·금융위기 수치는 합성 CAPE/BIL 대체 자료를 사용한 스트레스 검증이며 실제 투자 가능 성과가 아니다.

재현: `uv run python src/legacy-python/validation/strategy30_hy_oas_review.py --krw --output-stem strategy30_hy_oas_krw` 및 `uv run python src/legacy-python/validation/strategy30_hy_oas_review.py`. 민감도는 `--lags`, `--cost`, `--hy-level`, `--hy-change`로 지정한다. 결과 CSV는 `results/strategy30_hy_oas*`에 저장되며 원시 ICE 데이터는 Git에 추가하지 않는다.

---

### High Yield OAS 백테스트 검토 인계

작성일: 2026-09-29

이 문서는 ChatGPT에서 검토한 미국 하이일드 신용스프레드(ICE BofA US High Yield Index Option-Adjusted Spread, FRED series `BAMLH0A0HYM2`)의 장기 데이터 확보 및 백테스트 적용 검토를 VS Code Codex에서 이어가기 위한 인계 문서다.

#### Codex가 먼저 할 일

1. 이 문서 전체를 읽는다.
2. 현재 작업 트리의 미커밋 변경사항을 확인하고 보존한다.
3. `src/legacy-python/downloader.py`, `docs/research/strategy28-mixed-tuning.md`, `docs/research/strategy28-failed-dip-long-bear-indicators.md`를 읽는다.
4. 운영 전략/YAML은 바로 변경하지 않는다.
5. 먼저 데이터 파이프라인과 연구용 비교 백테스트만 구현한다.
6. 결과가 나오면 BAA10Y 대비 HY OAS가 실제로 추가 정보가 있는지 검증하고, 과최적화 여부를 평가한다.

#### 확인된 사실

##### 1. 사용할 지표

대표 하이일드 신용스프레드는 다음 계열을 사용한다.

- 명칭: ICE BofA US High Yield Index Option-Adjusted Spread
- FRED series: `BAMLH0A0HYM2`
- 단위: percentage points
- 빈도: Daily
- 의미: 미국 하이일드 회사채의 옵션조정 스프레드(OAS). 하이일드 시장의 신용위험/유동성 스트레스 측정에 사용한다.
- FRED: https://fred.stlouisfed.org/series/BAMLH0A0HYM2

Yahoo Finance의 `HYG`, `JNK` 가격은 하이일드 채권 ETF 가격이지 OAS 자체가 아니다. ETF 가격에는 Treasury duration, coupon, ETF 수급 등이 섞이므로 신용스프레드 대용으로 동일하게 취급하지 않는다.

##### 2. Yahoo Finance에서 직접 받는 데이터가 아니다

현재 프로젝트의 일반 시장가격은 yfinance를 사용하지만 `BAMLH0A0HYM2`는 yfinance ticker로 처리하지 않는다. FRED/보관본을 별도 macro source로 취급한다.

##### 3. 현재 FRED의 장기 데이터 제한

FRED의 ICE BofA 일부 시리즈는 2026년부터 공개 히스토리가 최근 3년으로 제한되어 장기 백테스트에 현재 endpoint만 사용하면 2000/2008/2020을 잃는다.

현재 endpoint:
https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2

과거 공개 계열은 1996-12-31까지 내려갔다.

##### 4. 과거 데이터 확보 경로

제한 전에 저장된 FRED CSV의 Wayback snapshot이 존재한다.

예:
https://web.archive.org/web/20251104204105/https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2

장기 백테스트는 다음 구조를 우선 검토한다.

- historical base: 제한 전 FRED CSV snapshot
- current tail: 현재 FRED CSV
- overlap은 날짜 기준 deduplicate
- 가능하면 최신 FRED 값을 우선
- 최종 series를 날짜 오름차순으로 정렬

제3자 GitHub 등에 남은 복사본도 있으나 원본성 측면에서 Wayback에 보존된 FRED CSV를 우선한다.

주의: 데이터 권리는 ICE Data Indices에 있으므로 raw historical CSV를 공개 저장소에 그대로 재배포하는 방식은 피한다. 현재 프로젝트의 기존 관행처럼 연구용 외부 입력은 `tmp/` 등에 두고 git에 넣지 않는 방식을 우선 검토한다.

#### 현재 프로젝트와의 연결점

##### downloader.py

현재 `src/legacy-python/downloader.py`에는 `BAA10Y` 전용 처리 코드가 있다.

현재 동작 요약:

1. FRED `BAA10Y` CSV를 읽는다.
2. QQQ 거래일 index로 `ffill` 정렬한다.
3. `.shift(1)`로 한 QQQ session 지연한다.
4. Close/Open/High/Low 동일 값인 pseudo-market frame으로 저장한다.
5. 이후 기존 Backtest가 일반 ticker와 동일한 형태로 읽을 수 있게 한다.

HY OAS도 이 구조를 재사용하는 것이 좋다. 단, `BAMLH0A0HYM2`는 장기 데이터 제한 때문에 historical+live merge가 필요하다.

가능하면 BAA10Y/HY OAS 공통 코드를 helper로 추출해 중복을 줄인다. 예:

- `_load_fred_series(...)`
- `_merge_historical_and_live_fred(...)`
- `_align_macro_to_qqq_sessions(..., lag_sessions=1)`

단, 기존 BAA10Y 결과가 변하지 않는지 회귀 테스트를 먼저 둔다.

#### Look-ahead bias

가장 중요하다.

FRED 관측일 label의 값이 그 날짜의 미국 주식 종가 이전에 실제 이용 가능했다고 가정하지 않는다.

현재 BAA10Y 연구는 보수적으로 QQQ 거래일에 맞춘 후 1 session shift를 적용한다. HY OAS도 기본 실험은 동일하게 적용한다.

최소 민감도 검증:

- lag 0 session: 참고용, 낙관적 가능성 있음
- lag 1 session: 기본
- lag 2 sessions: 보수적 민감도

운영 후보 판단은 lag 1/2에서도 방향이 유지되는지를 중시한다.

#### 백테스트에서 우선 만들 feature

단순히 `HY_OAS > 4%` 하나를 쓰지 않는다.

우선 아래 연속형 feature를 계산한다.

- `HY_OAS_LEVEL`: 현재 OAS
- `HY_OAS_DIFF_5`: 5거래일 변화
- `HY_OAS_DIFF_20`: 20거래일 변화
- `HY_OAS_MA20`: 20일 이동평균
- `HY_OAS_Z252`: 252거래일 rolling z-score
- 선택: 60일 고점 대비 현재 spread 축소폭

예시:

```python
hy["HY_OAS_DIFF_5"] = hy["Close"].diff(5)
hy["HY_OAS_DIFF_20"] = hy["Close"].diff(20)
mean252 = hy["Close"].rolling(252).mean()
std252 = hy["Close"].rolling(252).std()
hy["HY_OAS_Z252"] = (hy["Close"] - mean252) / std252
```

고정 임계값과 상대 임계값을 둘 다 비교한다. 특정 위기 2~3개를 보고 threshold를 고정하면 과최적화 위험이 크다.

#### 기존 BAA10Y 연구와 비교해야 할 것

현재 연구 문서에서 BAA10Y는 다음 형태로 사용됐다.

- level >= 2.0%p
- 20거래일 확대 >= 0.30%p
- QQQ/SPY 상대약세와 결합
- 신용 회복/가격 회복을 이용한 해제 조건
- 1/2거래일 발표 지연 민감도

HY OAS 도입 목적은 BAA10Y를 무조건 대체하는 것이 아니다.

아래 네 모델을 동일한 연구 구간에서 비교한다.

A. 기존 BAA10Y
B. HY OAS만
C. BAA10Y + HY OAS
D. 신용지표 없음 (가격/상대강도만)

평가해야 할 질문:

1. HY OAS가 BAA10Y보다 더 빠르게 위험 확대를 잡는가?
2. 코로나/2025처럼 QQQ 상대약세가 약한 급락에서 신용경보가 의미 있는가?
3. HY OAS 때문에 정상 조정에서 false positive가 늘어나는가?
4. 신호가 빨라져도 방어 해제가 늦어 CAGR을 훼손하지 않는가?
5. 2000, 2008, 2020, 2022뿐 아니라 rolling window에서도 개선이 반복되는가?
6. 1/2일 lag, 거래비용 증가, threshold 주변 perturbation에도 결론이 유지되는가?

#### 검증 구간

가능한 historical OAS가 1996년 말부터이므로 다음 사건을 포함할 수 있다.

- 1998 러시아/LTCM
- 2000~2002 닷컴
- 2007~2009 금융위기
- 2011 유럽 재정위기
- 2015~2016 credit stress
- 2018 Q4
- 2020 코로나
- 2022 약세장
- 2025 급락
- 최근 2026

한두 위기에서만 좋아지는 규칙을 운영 승격하지 않는다.

#### 구현 제안

##### 단계 1: 데이터 로더

`BAMLH0A0HYM2`를 macro series로 다운로드/생성할 수 있게 한다.

권장 로컬 파일 예:
`tmp/BAMLH0A0HYM2_historical.csv`

현재 FRED tail은 실행 시 받아 합친다.

데이터 merge는 다음 invariant를 가진다.

- date unique
- ascending
- numeric spread only
- historical 시작일이 예상보다 늦으면 경고/실패
- overlap 날짜에서 두 source 차이가 크면 검사 가능
- current FRED 장애 시 historical file 자체를 덮어쓰지 않는다.

##### 단계 2: 회귀 테스트

최소 테스트 후보:

- `test_baa10y_output_unchanged_after_macro_refactor`
- `test_hy_oas_merges_historical_and_live_without_duplicates`
- `test_hy_oas_prefers_live_value_on_overlap`
- `test_hy_oas_aligns_to_qqq_sessions`
- `test_hy_oas_default_lag_is_one_session`
- `test_hy_oas_missing_live_source_does_not_destroy_historical_input`

##### 단계 3: 연구용 스크립트

운영 strategy를 직접 수정하지 말고 `src/legacy-python/validation/`에 새 연구 스크립트를 둔다.

예:
`strategy28_hy_oas_review.py`

기존 `strategy28_failed_dip_regime.py`, `strategy28_credit_release_review.py`, `strategy28_mixed_tuning.py`의 구조를 최대한 재사용한다.

##### 단계 4: 결과

최소 산출물:

- summary CSV
- event dates CSV
- transitions CSV
- rolling-window comparison CSV

그리고 `docs/research/`에 결과 문서를 작성한다.

#### 권장 실험 순서

1. 데이터의 정확성/기간/lag부터 검증
2. 기존 BAA10Y 규칙에 HY OAS를 단순 치환하여 비교
3. HY OAS level/diff/z-score 각각 단독 비교
4. BAA10Y와 HY OAS 동시 사용이 정말 추가정보를 주는지 비교
5. threshold 민감도
6. rolling 3년/5년
7. 위기별 event timing
8. 비용 3배, lag 2일 등 stress
9. OOS/운영 승격 여부 판단

#### 하지 말아야 할 것

- HYG 가격을 HY OAS와 같은 것으로 취급하지 않는다.
- 현재 FRED 3년치만으로 장기 threshold를 튜닝하지 않는다.
- 2000/2008/2020 결과를 본 뒤 최적 threshold 하나를 골라 그대로 운영 규칙으로 승격하지 않는다.
- raw ICE historical dataset을 공개 repo에 커밋하지 않는다.
- 기존 잠긴/OOS 전략을 연구 단계에서 수정하지 않는다.
- 발표 시점 문제를 무시하고 same-day signal로 성과를 과대평가하지 않는다.

#### Codex에 줄 실행 요청

다음 요청으로 작업을 이어가면 된다.

> 이 문서 `docs/research/high-yield-oas-handoff-2026-09-29.md`와 현재 repository 코드를 읽어라. 기존 BAA10Y 데이터 처리와 Strategy 28 신용스프레드 연구를 보존하면서, BAMLH0A0HYM2 장기 historical archive + 현재 FRED tail을 결합하는 데이터 파이프라인을 설계하고 테스트하라. 먼저 구현 계획과 현재 코드에서 손댈 파일을 제시한 뒤 데이터/회귀 테스트를 구현하고, 운영 전략을 변경하지 않은 상태에서 BAA10Y vs HY OAS 비교 연구를 실행하라. look-ahead 방지를 위해 기본 1 QQQ session lag를 사용하고 0/2 session 민감도도 계산하라. raw ICE 데이터는 repo에 커밋하지 말고 외부 입력으로 처리하라. 결과가 나오면 단순 최고 CAGR을 고르지 말고 MDD, Sharpe/Calmar, 신호 수, 위기별 진입/해제 시점, rolling 3/5년, 비용/lag/threshold 민감도와 과최적화 위험을 함께 평가하라.

#### 참고

기존 프로젝트에는 이미 Google Apps Script에서 FRED `BAMLH0A0HYM2`를 조회하는 `GET_HIGH_YIELD_DATA()`가 있다. 이는 실시간/최근 표시용이며 장기 백테스트 데이터 확보 문제와는 별개다.
