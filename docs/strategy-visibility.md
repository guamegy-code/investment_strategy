# 전략 목록 노출 설정

전략 목록의 노출 여부는 두 단계로 결정됩니다.

1. 각 YAML의 `strategy.enabled`가 `false`이면 노출하지 않습니다.
2. `strategies/manifest.json`의 `hidden_strategy_ids`에 전략 ID가 있으면 노출하지 않습니다.

따라서 전략이 목록에 보이려면 YAML의 `enabled`가 `false`가 아니고, 중앙 숨김 목록에도 없어야 합니다. `enabled`를 생략하면 `true`로 취급합니다.

여러 전략을 한꺼번에 숨길 때는 각 YAML을 수정하는 대신 다음처럼 중앙 목록만 편집합니다.

```json
{
  "version": 1,
  "hidden_strategy_ids": [
    "qqq-structural-defense-balanced",
    "qqq-valuation-breakdown-defense"
  ]
}
```

`hidden_strategy_ids`에는 파일명이 아니라 YAML의 `strategy.id`를 적습니다. 숨김은 화면의 전략 목록에만 적용됩니다. 숨긴 전략도 다른 상품 매핑 전략의 `source`로 사용할 수 있습니다.

정적 사이트를 빌드하면 배포용 `dist/web/strategies/manifest.json`도 같은 숨김 목록과 전체 전략 파일 목록을 포함한 읽기 쉬운 여러 줄 JSON으로 생성됩니다. 직접 수정할 원본은 `strategies/manifest.json`입니다.
