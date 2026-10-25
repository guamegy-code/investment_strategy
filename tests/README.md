# 테스트

- `core/`: 데이터 로더, 백테스트 실행 기반, 알림, 공통 경로
- `strategies/`: 전략 규칙과 상품 매핑
- `research/`: 연구 실험, 신호, OOS 검증
- `web/`: 차트, 연구 화면, 오프라인 내보내기, 브라우저 런타임

프로젝트 루트에서 실행합니다. Python 경로는 `tests/__init__.py`에서 설정합니다.

```powershell
python -m unittest discover -s tests -t .
python -m unittest tests.core.test_notifier -v
node --test data-proxy/test/*.test.js tests/web/web_runtime.test.mjs
```

Worker 전용 테스트는 관련 소스와 가까운 `data-proxy/test/`에 둡니다.
`validation/`의 JSON은 테스트 코드가 아니라 연구 기준을 고정하는 파일입니다.
