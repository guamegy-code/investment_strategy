# High Yield OAS 백테스트 검토 인계

작성일: 2026-09-29

이 문서는 ChatGPT에서 검토한 미국 하이일드 신용스프레드(ICE BofA US High Yield Index Option-Adjusted Spread, FRED series `BAMLH0A0HYM2`)의 장기 데이터 확보 및 백테스트 적용 검토를 VS Code Codex에서 이어가기 위한 인계 문서다.

## Codex가 먼저 할 일

1. 이 문서 전체를 읽는다.
2. 현재 작업 트리의 미커밋 변경사항을 확인하고 보존한다.
3. `src/legacy-python/downloader.py`, `docs/research/strategy28-mixed-tuning.md`, `docs/research/strategy28-failed-dip-long-bear-indicators.md`를 읽는다.
4. 운영 전략/YAML은 바로 변경하지 않는다.
5. 먼저 데이터 파이프라인과 연구용 비교 백테스트만 구현한다.
6. 결과가 나오면 BAA10Y 대비 HY OAS가 실제로 추가 정보가 있는지 검증하고, 과최적화 여부를 평가한다.

## 확인된 사실

### 1. 사용할 지표

대표 하이일드 신용스프레드는 다음 계열을 사용한다.

- 명칭: ICE BofA US High Yield Index Option-Adjusted Spread
- FRED series: `BAMLH0A0HYM2`
- 단위: percentage points
- 빈도: Daily
- 의미: 미국 하이일드 회사채의 옵션조정 스프레드(OAS). 하이일드 시장의 신용위험/유동성 스트레스 측정에 사용한다.
- FRED: https://fred.stlouisfed.org/series/BAMLH0A0HYM2

Yahoo Finance의 `HYG`, `JNK` 가격은 하이일드 채권 ETF 가격이지 OAS 자체가 아니다. ETF 가격에는 Treasury duration, coupon, ETF 수급 등이 섞이므로 신용스프레드 대용으로 동일하게 취급하지 않는다.

### 2. Yahoo Finance에서 직접 받는 데이터가 아니다

현재 프로젝트의 일반 시장가격은 yfinance를 사용하지만 `BAMLH0A0HYM2`는 yfinance ticker로 처리하지 않는다. FRED/보관본을 별도 macro source로 취급한다.

### 3. 현재 FRED의 장기 데이터 제한

FRED의 ICE BofA 일부 시리즈는 2026년부터 공개 히스토리가 최근 3년으로 제한되어 장기 백테스트에 현재 endpoint만 사용하면 2000/2008/2020을 잃는다.

현재 endpoint:
https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2

과거 공개 계열은 1996-12-31까지 내려갔다.

### 4. 과거 데이터 확보 경로

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

## 현재 프로젝트와의 연결점

### downloader.py

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

## Look-ahead bias

가장 중요하다.

FRED 관측일 label의 값이 그 날짜의 미국 주식 종가 이전에 실제 이용 가능했다고 가정하지 않는다.

현재 BAA10Y 연구는 보수적으로 QQQ 거래일에 맞춘 후 1 session shift를 적용한다. HY OAS도 기본 실험은 동일하게 적용한다.

최소 민감도 검증:

- lag 0 session: 참고용, 낙관적 가능성 있음
- lag 1 session: 기본
- lag 2 sessions: 보수적 민감도

운영 후보 판단은 lag 1/2에서도 방향이 유지되는지를 중시한다.

## 백테스트에서 우선 만들 feature

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

## 기존 BAA10Y 연구와 비교해야 할 것

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

## 검증 구간

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

## 구현 제안

### 단계 1: 데이터 로더

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

### 단계 2: 회귀 테스트

최소 테스트 후보:

- `test_baa10y_output_unchanged_after_macro_refactor`
- `test_hy_oas_merges_historical_and_live_without_duplicates`
- `test_hy_oas_prefers_live_value_on_overlap`
- `test_hy_oas_aligns_to_qqq_sessions`
- `test_hy_oas_default_lag_is_one_session`
- `test_hy_oas_missing_live_source_does_not_destroy_historical_input`

### 단계 3: 연구용 스크립트

운영 strategy를 직접 수정하지 말고 `src/legacy-python/validation/`에 새 연구 스크립트를 둔다.

예:
`strategy28_hy_oas_review.py`

기존 `strategy28_failed_dip_regime.py`, `strategy28_credit_release_review.py`, `strategy28_mixed_tuning.py`의 구조를 최대한 재사용한다.

### 단계 4: 결과

최소 산출물:

- summary CSV
- event dates CSV
- transitions CSV
- rolling-window comparison CSV

그리고 `docs/research/`에 결과 문서를 작성한다.

## 권장 실험 순서

1. 데이터의 정확성/기간/lag부터 검증
2. 기존 BAA10Y 규칙에 HY OAS를 단순 치환하여 비교
3. HY OAS level/diff/z-score 각각 단독 비교
4. BAA10Y와 HY OAS 동시 사용이 정말 추가정보를 주는지 비교
5. threshold 민감도
6. rolling 3년/5년
7. 위기별 event timing
8. 비용 3배, lag 2일 등 stress
9. OOS/운영 승격 여부 판단

## 하지 말아야 할 것

- HYG 가격을 HY OAS와 같은 것으로 취급하지 않는다.
- 현재 FRED 3년치만으로 장기 threshold를 튜닝하지 않는다.
- 2000/2008/2020 결과를 본 뒤 최적 threshold 하나를 골라 그대로 운영 규칙으로 승격하지 않는다.
- raw ICE historical dataset을 공개 repo에 커밋하지 않는다.
- 기존 잠긴/OOS 전략을 연구 단계에서 수정하지 않는다.
- 발표 시점 문제를 무시하고 same-day signal로 성과를 과대평가하지 않는다.

## Codex에 줄 실행 요청

다음 요청으로 작업을 이어가면 된다.

> 이 문서 `docs/research/high-yield-oas-handoff-2026-09-29.md`와 현재 repository 코드를 읽어라. 기존 BAA10Y 데이터 처리와 Strategy 28 신용스프레드 연구를 보존하면서, BAMLH0A0HYM2 장기 historical archive + 현재 FRED tail을 결합하는 데이터 파이프라인을 설계하고 테스트하라. 먼저 구현 계획과 현재 코드에서 손댈 파일을 제시한 뒤 데이터/회귀 테스트를 구현하고, 운영 전략을 변경하지 않은 상태에서 BAA10Y vs HY OAS 비교 연구를 실행하라. look-ahead 방지를 위해 기본 1 QQQ session lag를 사용하고 0/2 session 민감도도 계산하라. raw ICE 데이터는 repo에 커밋하지 말고 외부 입력으로 처리하라. 결과가 나오면 단순 최고 CAGR을 고르지 말고 MDD, Sharpe/Calmar, 신호 수, 위기별 진입/해제 시점, rolling 3/5년, 비용/lag/threshold 민감도와 과최적화 위험을 함께 평가하라.

## 참고

기존 프로젝트에는 이미 Google Apps Script에서 FRED `BAMLH0A0HYM2`를 조회하는 `GET_HIGH_YIELD_DATA()`가 있다. 이는 실시간/최근 표시용이며 장기 백테스트 데이터 확보 문제와는 별개다.

