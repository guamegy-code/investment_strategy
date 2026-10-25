# 레거시 Python 분석 코드

이 디렉터리는 과거의 Python 기반 백테스트, 분석 화면, 검증 도구를 보관합니다.
현재 서비스 운영과 배포에는 사용하지 않습니다.

현재 제공되는 기능은 다음 디렉터리에 있습니다.

- `src/web/`: 브라우저에서 실행되는 정적 전략 검토 화면
- `data-proxy/`: 가격 조회 및 알림 평가 Cloudflare Worker

필요한 경우 레거시 코드를 실행할 때는 `src/legacy-python`을 Python 모듈 경로에 추가해야
합니다.

## 로컬 데이터

실행 결과는 `results/backtests/`, 연구 보고서는 `results/research/<주제>/`에
저장합니다. 연구 주제는 기존 파일명의 첫 `_` 앞부분이며, `strategy30_*.csv`는
`results/research/strategy30/`에 모입니다. 연구 코드에서는 `result_paths.py`의
`research_result_path()`로 같은 경로를 읽고 씁니다.
차트 이미지는 `results/charts/`, 차트 선택 설정은 `results/state/`, 계산 캐시는
`results/cache/`에 저장합니다. 생성 결과는 Git에서 제외합니다.
테스트 구성과 실행 명령은 [tests/README.md](../../tests/README.md)를 참고하세요.

기본 시장 데이터는 프로젝트 루트의 `data/`, 연구 데이터셋은 `data/research/`에
보관합니다. 경로는 `config.py`에서 정의합니다.

```text
data/research/
  extended/                  ETF 설정 전 기간을 보완한 합성 데이터
  multimarket/               다중 시장 확인용 데이터
  risk_assets/               위험자산 비교 데이터
  probability_signals/       변동성·신용·시장 심리 지표
  cross_asset_rotation/      자산군 회전 연구 데이터
  cross_sectional_momentum/  종목 간 모멘텀 비교 데이터
```

각 데이터셋은 기간·합성 방식·통화 기준이 다를 수 있으므로 같은 종목명 파일도
별도로 유지합니다. 연구 데이터는 로컬에서 생성·보관하며 Git에서 제외합니다.
