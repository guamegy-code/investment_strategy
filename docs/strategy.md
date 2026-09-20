# YAML 전략 안내

여러 전략의 목록 노출을 한곳에서 관리하려면 [전략 목록 노출 설정](strategy-visibility.md)을 참고하세요.

이 문서는 `strategies/`의 YAML 파일을 사람이 읽기 쉽게 정리한 안내서입니다. 실행 규칙의
진실 원천은 항상 YAML이며, 문서와 YAML이 다르면 YAML을 우선합니다. YAML 문법과 필드의
의미는 [전략 DSL](strategy-dsl.md)을 참고하세요.

`enabled: true`인 전략은 기본 전략 목록에 자동으로 로드됩니다. `false`인 전략은 비교·탐색용으로
보관하며, YAML 파일을 직접 가져오면 검토할 수 있습니다. 모든 결과는 연구용 백테스트이며 투자
권유가 아닙니다.

## 전략 작성자 빠른 시작

1. `strategies/`에서 구조가 가장 비슷한 YAML을 복사하거나
   [전략 DSL의 심플 예제](strategy-dsl.md#심플-예제)로 시작합니다.
2. `strategy.id`를 영문·숫자·하이픈 조합의 고유한 값으로 정하고, 규칙을 수정할 때는
   `strategy.version`도 올립니다. 파일명은 실행 의미나 전략 ID를 대신하지 않습니다.
3. 웹페이지의 **전략 가져오기**로 YAML을 불러와 문법 오류, 필요한 시장 데이터, 목표 비중과
   리밸런싱 시점을 확인합니다.
4. 공개 목록에 넣을 전략은 `strategies/`에 저장하고 `strategy.enabled`와
   `strategies/manifest.json`의 숨김 목록을 확인합니다. 개인 전략은 저장소에 추가하지 않습니다.
5. 알림이 필요한 전략은 `notifications`의 표시 상태, 사전주의 조건과 정기 브리핑 주기를
   확인합니다.
6. 검토가 끝나면 서버 운영자에게 정적 사이트 재빌드·배포를 요청합니다. 배포 절차는
   [Cloudflare 데이터 프록시 및 배포](data-proxy.md#내용-변경-후-업데이트)에 있습니다.

기존 전략의 계산 규칙을 바꾸면 전략 ID를 구독 중인 사용자에게 변경 사실과 상태 재초기화
필요 여부를 알려야 합니다. 계산 상태의 의미가 달라졌다면 사용자는 배포 후
`initializeNotificationStates()`를 다시 실행해야 합니다.

## 빠른 목록

| 파일 | 전략 ID | 자동 로드 | 역할 |
|---|---|---:|---|
| `01_allocation.yaml` | `allocation` | 아니요 | BND/BIL을 고르는 기본 4상태 70/30 |
| `02_allocation_vxus.yaml` | `allocation-vxus` | 아니요 | 방어 구간의 BND 일부를 VXUS로 바꾸는 기본형 |
| `03_profit_band.yaml` | `profit-band` | 아니요 | QQQ 수익 비중을 상한까지 유지하는 BND/BIL형 |
| `04_profit_band_vxus.yaml` | `profit-band-vxus` | 예 | 수익 밴드와 방어 구간 VXUS 대체 |
| `05_profit_band_vxus_v2.yaml` | `profit-band-vxus-v2` | 예 | VXUS형의 상태·안전자산 혼합 조정판 |
| `05P_profit_band_time_tdf2050.yaml` | `time-vxus-tdf2050` | 예 | V2 신호를 국내 TIME Nasdaq·KODEX TDF2050에 매핑 |
| `06_profit_band_tdf2050.yaml` | `profit-band-tdf2050` | 예 | TDF2050·BND·BIL 안전 슬리브를 쓰는 4상태 수익 밴드 |
| `06P_profit_band_time_tdf2050.yaml` | `time-tdf2050-profit-band` | 예 | 06의 QQQ/TDF 슬리브를 국내 상품에 매핑 |
| `07_band_7030_bnd.yaml` | `band-7030` | 예 | QQQ/BND 고정 70/30 기준 전략 |
| `08_band_7030_tdf.yaml` | `band-7030-tdf` | 예 | QQQ/TDF2050 프록시 고정 70/30 기준 전략 |
| `08P_band_7030_nasdaq_tdf2050.yaml` | `nasdaq-tdf2050-7030` | 예 | 08을 국내 TIME Nasdaq·KODEX TDF2050에 매핑 |
| `09_static_7030.yaml` | `static-7030` | 아니요 | QQQ 70%·BND 15%·BIL 15% 고정 비교 전략 |
| `10_trend_band_defense.yaml` | `trend-band-defense` | 아니요 | 추세 밴드·고점 대비 하락을 쓰는 방어 후보 |
| `12_profit_band_tdf2050_gate_spy.yaml` | `profit-band-tdf2050-gate-spy` | 예 | 06의 CAUTION 진입에 SPY 확인을 추가한 후보 |
| `13_profit_band_tdf2050_gate_spy_tdf100.yaml` | `profit-band-tdf2050-gate-spy-tdf100` | 예 | 12에서 비위험 슬리브를 회복 후 전부 TDF로 복원 |
| `14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml` | `profit-band-tdf2050-gate-spy-tdf100-no-bnd` | 예 | 13에서 BND를 빼고 TDF/BIL만 사용하는 전용 단순형 |
| `15_band_7030_tdf_state_bil.yaml` | `band-7030-tdf-state-bil` | 예 | 70/30 밴드에 사전 BEAR 현금화와 상태별 TDF/BIL 배분을 결합한 방어형 |
| `16_state_conditioned_cross_asset_rotation.yaml` | `state-conditioned-cross-asset-rotation` | 예 | Strategy 15의 QQQ/TDF 비중을 보존하고 BIL 슬리브만 자동 교차자산으로 대체 |
| `27_buy_3dip_valuation_defense_80_98_99_100.yaml` | `buy-3dip-bil-valuation-hybrid-80-98-99-100` | 예 | 3단계 매수·회복 규칙에 밸류에이션 방어와 80/98/99/100 비중을 결합한 개인연금 전략 |
| `23_qqq_structural_defense_balanced.yaml` | `qqq-structural-defense-balanced` | 예 | 개인연금용 QQQ 100%와 4상태 추세 방어를 결합한 23B |
| `24_qqq_valuation_breakdown_defense.yaml` | `qqq-valuation-breakdown-defense` | 예 | 합성 밸류에이션 고점 붕괴를 주 방어 신호로 쓰고 23번 추세 상한을 결합한 전략 |
| `25_qqq_valuation_breakdown_balanced.yaml` | `qqq-valuation-breakdown-balanced` | 예 | 월간 밸류에이션 급락과 빠른 가격 확인을 결합한 개인연금 균형형 |
| `25P_qqq_valuation_breakdown_balanced_kodex_koact.yaml` | `qqq-valuation-breakdown-balanced-kodex-koact` | 예 | 25의 QQQ를 KODEX·KoAct에 반반, BIL을 KODEX 머니마켓에 매핑 |
| `26_band_7030_tdf_valuation_defense.yaml` | `band-7030-tdf-valuation-defense` | 예 | 15번 퇴직연금 70/30 구조에 25번 밸류에이션 방어를 결합한 전략 |
| `26P_band_7030_tdf_valuation_defense_kodex_koact.yaml` | `band-7030-tdf-valuation-defense-kodex-koact` | 예 | 26의 QQQ를 KODEX·KoAct에 반반, TDF·BIL을 국내 상품에 매핑 |

다음 파일들은 독립 규칙이 아니라 `source` 전략의 신호·상태·리밸런싱·알림 규칙을 그대로 사용해 실제
상품만 바꾸는 매핑입니다. 자세한 매핑은 [국내 상품 매핑](#국내-상품-매핑)에 있습니다.

| 파일 | 전략 ID | 기준 전략 | 자동 로드 |
|---|---|---|---:|
| `04P_kodex_nasdaq.yaml` | `kodex-nasdaq` | `profit-band-vxus` | 아니요 |
| `04P_time_nasdaq.yaml` | `time-nasdaq` | `profit-band-vxus` | 아니요 |
| `04P_koact_nasdaq.yaml` | `koact-nasdaq` | `profit-band-vxus` | 아니요 |
| `04P_nasdaq_mix.yaml` | `nasdaq-mix` | `profit-band-vxus` | 아니요 |
| `05P_profit_band_time_tdf2050.yaml` | `time-vxus-tdf2050` | `profit-band-vxus-v2` | 아니요 |
| `06P_profit_band_time_tdf2050.yaml` | `time-tdf2050-profit-band` | `profit-band-tdf2050` | 예 |
| `08P_band_7030_nasdaq_tdf2050.yaml` | `nasdaq-tdf2050-7030` | `band-7030-tdf` | 아니요 |
| `19P_buy_3dip_buyer_tdf.yaml` | `buy-3dip-tdf` | `buy-3dip-bil` | 아니요 |
| `27P_buy_3dip_80_98_99_100_active.yaml` | `buy-3dip-bil-valuation-hybrid-active` | `buy-3dip-bil-valuation-hybrid-80-98-99-100` | 예 |
| `25P_qqq_valuation_breakdown_balanced_kodex_koact.yaml` | `qqq-valuation-breakdown-balanced-kodex-koact` | `qqq-valuation-breakdown-balanced` | 예 |
| `26P_band_7030_tdf_valuation_defense_kodex_koact.yaml` | `band-7030-tdf-valuation-defense-kodex-koact` | `band-7030-tdf-valuation-defense` | 예 |

## 공통 용어와 상태 전이

### 자산

- `QQQ`: 주요 위험자산이자 시장 상태 판정의 기준입니다.
- `BND`, `BIL`: 채권·현금성 안전 슬리브 후보입니다.
- `VXUS`: 일부 방어 구간에서 BND의 일부를 대체하는 위험자산입니다.
- `TDF2050_PROXY`: TDF2050을 연구·백테스트하기 위한 합성 자산입니다. 구성 방식은
  [전략 DSL의 자산 설명](strategy-dsl.md#자산과-티커)을 따릅니다.
- `SPY`: Gate SPY 전략에서만 CAUTION 진입을 확인하는 관측 자산입니다. 목표 비중에는
  포함되지 않습니다.

### 네 가지 시장 상태

`allocation` 계열과 `profit-band` 계열은 `market_mode`를 매 거래일 평가합니다. 첫 평가에서는
확인 기간 없이 현재 조건으로 상태를 고르고, 이후 전이는 아래 조건을 연속으로 만족해야 합니다.

| 현재 상태 | 다음 상태 | 조건 | 확인 |
|---|---|---|---:|
| BULL | CAUTION | 전략별 조기 경고 조건 | 2~3일, Gate SPY는 1일 |
| BULL 또는 CAUTION | BEAR | 장기 하락 조건 | 10일 |
| CAUTION | BULL | `recovery_score` 4 이상 | 3일 |
| BEAR | RECOVERY | `recovery_score` 3 이상 | 2일 |
| RECOVERY | BULL | `recovery_score` 4 이상, QQQ가 EMA55 위, ROC60 양수 | 3일 |
| RECOVERY | CAUTION | `risk_off_score` 5 이상 | 3일 |
| RECOVERY | BEAR | 장기 하락 조건 | 10일 |

`risk_off_score`는 아래 6개 QQQ 약세 조건의 충족 개수이고, `recovery_score`는 반대의 강세
조건 충족 개수입니다.

1. 종가가 EMA20 아래/위
2. 종가가 EMA55 아래/위
3. EMA20이 EMA55 아래/위
4. ROC5가 음수/양수
5. ROC20이 음수/양수
6. EMA20의 5일 기울기가 음수/양수

`structural_bear`는 `risk_off_score` 5 이상에 더해 QQQ가 EMA20 아래, EMA20 < EMA55 <
EMA200, ROC60 음수, EMA200의 20일 기울기 음수인 경우입니다. `allocation`·`profit-band`
초기형은 여기에 최근 120일 고점 대비 낙폭 -8% 이하 조건을 추가합니다. V2와 TDF 계열은 이
낙폭 조건을 사용하지 않습니다.

### 상태별 위험 비중과 실행 기간

01~14 네 상태 계열의 기준 위험 비중은 동일합니다. 단, 수익 밴드 조건을 충족하면
BULL/CAUTION에서 QQQ를 즉시 70%로 되돌리지 않을 수 있습니다. 15는 RECOVERY 비중과 모든
상태의 실행기간이 다르며 [15번 절](#15--band-7030-tdf-state-bil)에 별도로 적습니다.

| 상태 | QQQ 기준 목표 | 리밸런싱 분할 일수 |
|---|---:|---:|
| BULL | 70% | 5일 |
| CAUTION | 70% | 2일 |
| BEAR | 0% | 1일 |
| RECOVERY | 50% | 3일 |

## 수익 밴드 규칙

`profit-band` 계열은 BULL·CAUTION에서 QQQ 비중이 기준 비중과 상한 사이에 있으면 그 실제
비중을 목표로 유지합니다. 상승 중인 QQQ를 기계적으로 70%로 되파는 일을 줄이기 위한 규칙입니다.

| 계열 | 유지 구간 | 상한 도달 시 | CAUTION 제한 |
|---|---|---|---|
| `profit-band`, `profit-band-vxus`, `profit-band-vxus-v2` | 70% 초과 ~ 80% 미만 | QQQ 80% 이상이면 기준 목표로 복원 | 없음 |
| TDF2050 계열 06·12·13·14 | 70% 초과 ~ 77.5% 미만 | QQQ 77.5% 이상이면 기준 목표로 복원 | QQQ가 75%를 넘으면 수익 밴드를 적용하지 않음 |

상태가 바뀌거나 월간 점검일이 와도 수익 밴드 안에 있으면 일반적인 주문은 생략합니다. 다만
상한 도달, 안전 슬리브 변경 또는 문서에 적은 별도 조건은 주문을 만들 수 있습니다.

## TDF2050 Gate SPY 계열

### 공통 목표 구조

`profit-band-tdf2050`와 12~14는 앞 절의 네 상태·수익 밴드를 사용합니다. 위험 비중 이외의
슬리브는 `safe_tdf_share`, `bond_share`, `bnd_capacity`로 나눕니다.

- `safe_tdf_share`: 비-QQQ 슬리브 중 TDF2050 프록시의 비중입니다.
- `bond_share`: TDF가 아닌 안전 슬리브에서 BND를 쓸지 결정합니다. 매월 BND ROC40이
  BIL ROC40보다 1.50%p 초과하고 BND 종가가 EMA55 위인 상태가 2회 확인되면 100%가 됩니다.
  BND ROC40이 BIL ROC40 이하인 상태가 2회 확인되면 0%로 돌아갑니다.
- `bnd_capacity`: CAUTION에서 TDF 비중이 0%일 때 BND 몫을 50%로 제한하고 나머지를 BIL에
  둡니다. 14에는 BND가 없으므로 이 상태도 없습니다.

`safe_tdf_share`가 0%로 바뀌는 경우와 `bond_share` 또는 `bnd_capacity`가 바뀌는 경우에는
안전 슬리브만 1일에 조정합니다. 일반적인 상태 전환은 목표 편차가 2%p 이상일 때만 주문하며,
월간 편차 점검 기준은 5%p입니다.

### 06 — Profit Band TDF 2050

`06_profit_band_tdf2050.yaml`은 TDF2050·BND·BIL을 함께 쓰는 기준 TDF형입니다.

- BULL과 CAUTION에서 비-QQQ 슬리브의 82%를 TDF2050 프록시에 둡니다.
- CAUTION에서 `risk_off_score`가 5 이상이면 TDF 비중을 즉시 0%로 낮춥니다.
- 그 뒤 `recovery_score` 3 이상이 2일 이어지면 TDF 비중을 82%로 복원합니다.
- 남는 슬리브는 앞의 BND/BIL 규칙으로 분배합니다.
- BULL에서 CAUTION으로 갈 때는 `risk_off_score` 5 이상이 2일 연속이어야 합니다.

### 12 — Profit Band TDF 2050 Gate SPY

`12_profit_band_tdf2050_gate_spy.yaml`은 06과 목표·안전 슬리브 규칙이 같고, BULL에서
CAUTION으로 들어가는 조기 경고만 바꿉니다.

QQQ 종가가 EMA20 아래이고 `risk_off_score`가 3 이상인 상태에서, SPY도 EMA20 아래이며
SPY ROC5가 -1% 이하이면 1일 확인으로 CAUTION에 들어갑니다. 즉 QQQ 단독 약세가 아니라 SPY
확인을 요구합니다. TDF 초기·복원 비중은 82%입니다.

### 13 — Gate SPY TDF 100

`13_profit_band_tdf2050_gate_spy_tdf100.yaml`은 12의 Gate SPY 규칙을 유지하되,
정상·회복 뒤 비-QQQ 슬리브를 TDF2050 프록시 100%로 둡니다.

- CAUTION에서 강한 약세가 발생하면 TDF 비중을 0%로 낮춥니다.
- `recovery_score` 3 이상이 2일 이어지면 TDF 비중을 100%로 복원합니다.
- TDF가 0%인 동안에는 BND/BIL 규칙을 사용합니다.

### 14 — Gate SPY TDF 100 No BND

`14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml`은 13을 단순화한 TDF/BIL 전용형입니다.

- Gate SPY, 4상태 전이, QQQ 수익 밴드와 75% CAUTION 상한은 13과 같습니다.
- 비-QQQ 슬리브는 TDF2050 프록시와 BIL만 사용합니다. BND의 추세 판정·용량 제한 상태는
  없습니다.
- 평상시와 회복 확인 뒤에는 비-QQQ 슬리브 전체가 TDF2050 프록시입니다.
- CAUTION에서 `risk_off_score` 5 이상이면 그 전체를 BIL로 옮기고, `recovery_score` 3 이상이
  2일 이어지면 다시 TDF2050 프록시 100%로 복원합니다.

### 15 — Band 70/30 TDF State BIL

`15_band_7030_tdf_state_bil.yaml`은 QQQ/TDF2050 프록시 70/30 밴드에 최신 Gate SPY 계열의
4상태 전이와 BIL 방어를 결합합니다. 14와 달리 수익 밴드와 `safe_tdf_share` 상태는 사용하지
않습니다.

- BULL과 CAUTION의 기준 목표는 QQQ 70%, TDF2050 프록시 30%입니다. 목표 편차가 7.5%p
  미만이면 리밸런싱하지 않으므로 상승한 QQQ나 변동한 TDF를 매일 70/30으로 강제 복원하지
  않습니다.
- `structural_bear`가 처음 발생하면 10일 BEAR 확인을 기다리는 동안 QQQ와 TDF를 모두
  BIL로 옮깁니다. 조건이 확인 전에 해제되면 현재 시장 상태의 원래 목표로 돌아갑니다.
- `structural_bear`가 10거래일 연속 유지되어 BEAR가 확정되면 QQQ 0%, TDF2050 프록시
  20%, BIL 80%를 목표로 합니다.
- BEAR에서 `recovery_score` 3 이상이 3일 이어지면 RECOVERY로 전환하여 QQQ 60%,
  TDF2050 프록시 30%, BIL 10%를 목표로 합니다.
- 모든 주문은 1일에 실행합니다. 첫 평가 시 이미 `structural_bear`이면 확인 대기 없이
  BEAR로 초기화되므로 바로 BEAR 목표를 사용합니다.

채택 근거와 비교 결과는 [15번 사전 BEAR 방어 및 리밸런싱 개선](research/band-7030-tdf-state-bil-experiments.md)에
기록합니다.

### 16 — State-conditioned Cross-asset Rotation

`16_state_conditioned_cross_asset_rotation.yaml`은 Strategy 15를 대체하지 않는 별도 전략입니다.
QQQ/TDF의 상태별 기본 비중은 15와 동일하게 BULL·CAUTION 70%/30%, 사전 BEAR 확인 대기
구간 70%/30%, BEAR 0%/20%/BIL 80%, RECOVERY 60%/30%/BIL 10%를 유지합니다.
BEAR 확정 전 `structural_bear` 신호만으로 BIL 100%로 이동하던 예외는 상태 확인 규칙과
충돌하고 과매매를 만들었기 때문에 제거했습니다.

- BIL 비중이 있을 때만 매월 첫 평가일에 후보를 자동 검토합니다. 후보는 금, 미국 단기·중기채,
  국내 단기·중기채, KOSPI 200, 선진국·신흥국 주식입니다.
- 가격이 EMA200 위이고 현지 통화 기준 3개월 수익률이 BIL보다 높은 후보만 적격입니다. 적격 후보는
  BIL 대비 3·6·12개월 초과수익률의 50%/30%/20% 가중 점수로 순위를 정합니다.
- 자산군별 상한(종목 50%, 금 30%, 주식 합계 30%)과 역변동성 비중을 적용한 뒤, 남은 몫은
  BIL에 남깁니다. QQQ와 TDF의 기본 목표 비중을 침범하지 않습니다.
- 종목 선택과 매매 판단은 완전 자동입니다. 월간 `RotationDecision` 기록에는 후보별 적격/탈락
  사유, 점수, 이전·새 선택, 최종 비중과 교체 설명이 남아 결과를 사람이 검토할 수 있습니다.
- 신규 후보는 기존 보유 후보보다 종합점수가 3점 이상 높아야 교체할 수 있고, 기존 후보는
  최소 3회 월간 검토 동안 유지합니다. 계산 비중 차이가 10%p 미만이면 기존 비중을 유지하며,
  BIL 슬리브가 0%가 되어도 선택은 휴면 상태로 기억합니다.
- 추세·모멘텀 신호는 각 자산의 현지 통화 가격으로 계산합니다. 실제 보유 가격·포트폴리오
  성과·목표 편차는 실행 시점에 `KRW=X`를 적용한 원화 기준으로 계산합니다. 종목별 `_KRW`
  파일은 만들지 않습니다.

초기 PoC의 수익률 개선만으로 Strategy 15를 바꾸지는 않았으며, 이 전략의 검증 근거와 한계는
[상태 조건부 교차자산 로테이션 PoC](research/state-conditioned-cross-asset-rotation-poc.md)에 기록합니다.

### 23 — QQQ Structural Defense Balanced

`23_qqq_structural_defense_balanced.yaml`은 안전자산 비율 제한이 없는 개인연금에서 QQQ의
장기 수익 참여를 유지하면서 구조적 하락 구간만 BIL로 방어하는 전략입니다.

- BULL과 일반 CAUTION에서는 QQQ 100%를 목표로 합니다.
- `structural_bear`가 발생했지만 BEAR 10일 확인이 끝나지 않은 구간은 QQQ 50%, BIL 50%입니다.
- BEAR가 확정되면 QQQ 0%, BIL 100%로 전환합니다.
- RECOVERY에서는 QQQ 80%, BIL 20%를 유지하고, EMA55와 ROC60 회복까지 확인한 뒤 QQQ 100%로 돌아갑니다.
- 목표 비중과 실제 비중의 차이가 7.5%p 이상일 때 다음 거래일 시가에 1일 주문을 실행합니다.

### 24 — QQQ Valuation Breakdown Defense

`24_qqq_valuation_breakdown_defense.yaml`은 22번의 합성 밸류에이션 점수를 보조 필터가 아닌
주 방어 상태로 사용합니다. 단순히 점수가 높다는 이유로 QQQ를 줄이지 않고, 고평가 이후 점수가
급락하면서 실제 가격 약세까지 확인되는 전환 구간을 찾습니다.

- NORMAL에서 합성점수가 65 이상인 주기 고점을 만든 뒤 25점 이상 하락하면 WARNING이 됩니다.
- WARNING에서 `risk_off_score` 4 이상이 6거래일 연속이면 DEFENSE가 되고 QQQ 50%, BIL 50%를 목표로 합니다.
- `recovery_score` 4 이상과 QQQ의 EMA20 상향 회복이 3거래일 이어지면 NORMAL로 돌아가며, 그날 점수로 새 주기 고점을 시작합니다.
- 23번의 추세 상태는 안전 상한으로 유지합니다. 확정 BEAR는 QQQ 0%, 구조적 약세 사전 경보는 QQQ 50%, RECOVERY는 QQQ 80% 상한입니다.
- 여러 조건이 겹치면 QQQ 비중이 가장 낮은 목표를 우선합니다. 모든 주문은 다음 거래일 시가부터 1일에 실행합니다.

23·24번의 성과 비교, 비용·파라미터 민감도와 표본 한계는
[QQQ 구조적 방어와 밸류에이션 급락 방어 검증](research/qqq-structural-valuation-defense-validation.md)에 기록합니다.

### 25 — QQQ Valuation Breakdown Balanced

`25_qqq_valuation_breakdown_balanced.yaml`은 24번의 월간 밸류에이션 신호를 유지하면서
일별 가격 신호에 따라 방어 속도와 강도를 조절한 균형형 전략입니다.

- 밸류에이션 고점 65점과 고점 대비 25점 하락 기준은 24번과 같습니다.
- 밸류에이션 급락일에 `risk_off_score`가 6이면 WARNING을 건너뛰고 즉시 DEFENSE가 됩니다.
- 극단 약세가 아니면 WARNING에서 `risk_off_score` 4 이상을 6거래일 연속 확인합니다.
- DEFENSE 목표는 QQQ 30%, BIL 70%로 24번보다 방어 강도를 높입니다.
- `recovery_score` 4 이상과 QQQ의 EMA20 상향 회복이 2거래일 이어지면 NORMAL로 복귀합니다.
- 최대 절대 괴리 7.5%p는 유지하되 QQQ가 목표보다 4%p 이상 부족하면 먼저 매수하며,
  주문은 다음 거래일 시가에 체결합니다.

25번의 튜닝 범위, 구간별 성과와 제한된 방어 사건에 따른 과최적화 위험은
[QQQ 밸류에이션 방어 타이밍 균형형 검증](research/qqq-valuation-defense-timing-balanced.md)에 기록합니다.

### 26 — 70/30 TDF Valuation Defense

`26_band_7030_tdf_valuation_defense.yaml`은 15번의 퇴직연금 70/30 배분과 네 상태 방어에
25번의 밸류에이션 급락 방어를 추가한 전략입니다.

- 15번의 `market_mode`와 25번의 `trend_mode`는 규칙이 같으므로 `trend_mode` 하나만 사용합니다.
- 밸류에이션 고점 65점, 고점 대비 25점 하락, 극단 약세 즉시 진입과 일반 약세 6일 확인은
  25번과 같습니다.
- 밸류에이션 DEFENSE에서는 QQQ 30%, BIL 70%를 목표로 합니다.
- 15번의 구조적 약세 사전 경보 QQQ 0%·BIL 100%와 확정 BEAR QQQ 0%·TDF2050 20%·BIL
  80%가 밸류에이션 방어보다 우선합니다.
- 정상·주의 구간은 QQQ 70%·TDF2050 30%, RECOVERY는 QQQ 60%·TDF2050 30%·BIL 10%입니다.
- 위험자산으로 분류한 QQQ의 최대 비중은 70%이며, 주문은 다음 거래일 시가부터 1일에 실행합니다.
- 최대 절대 괴리 7.5%p는 유지하되 QQQ가 목표보다 4%p 이상 부족하면 먼저 매수합니다.

후보 배분, 구간별 성과, 롤링 및 비용 민감도와 표본 한계는
[26번 퇴직연금 밸류에이션 방어 검증](research/strategy26-retirement-valuation-defense.md)에 기록합니다.

## VXUS 수익 밴드 계열

### 04 — Profit Band VXUS

`04_profit_band_vxus.yaml`은 QQQ 수익 밴드와 BND/BIL 안전 슬리브를 결합한 뒤,
BEAR·RECOVERY에서 BND의 일부를 VXUS로 바꿉니다.

- BULL·CAUTION에서는 QQQ 70% 또는 수익 밴드의 실제 QQQ 비중을 쓰고, 남은 비중은 매월
  ROC40 비교로 고른 BND 또는 BIL 한 종목에 둡니다.
- BEAR·RECOVERY에서는 BND와 BIL ROC40 차이에 따라 BND 비중을 0%, 25%, 50%, 75%, 100% 중
  하나로 정합니다.
- 정해진 BND 몫 중 QQQ와 VXUS의 합계가 70%를 넘지 않는 범위는 VXUS로 대체합니다. 따라서
  BEAR에서는 BND 슬리브의 최대 70%가, RECOVERY에서는 최대 20%가 VXUS가 될 수 있습니다.
- BND와 BIL의 월간 선택은 상대 ROC40 차이가 0.25%p를 넘어야 변경합니다.

### 05 — Profit Band VXUS V2

`05_profit_band_vxus_v2.yaml`은 04와 같은 자산·수익 밴드·VXUS 대체 공식을 사용하며, 다음
세 가지를 바꿉니다.

- BULL → CAUTION 확인 기간을 3일에서 2일로 줄입니다.
- BND/BIL 전환 완충값을 0.25%p에서 0.20%p로 낮춥니다.
- BEAR·RECOVERY의 BND 비중을 0%, 50%, 100% 세 단계로 단순화하고, 100%를 선택하는 ROC40
  차이 기준을 1.50%p로 둡니다.

### 01~03 — 초기 비교형

`01_allocation.yaml`은 수익 밴드와 VXUS 없이 4상태에 따라 QQQ 70%/50%/0%를 쓰고, BND와
BIL 중 ROC40이 더 좋은 쪽을 안전자산으로 고릅니다. `02_allocation_vxus.yaml`은 그 방어
구간 BND 몫을 위험 한도(70%)까지 VXUS로 바꾸고, `03_profit_band.yaml`은 VXUS 없이 수익
밴드와 BND/BIL 혼합을 추가합니다. 세 전략은 현재 자동 로드하지 않습니다.

## 고정 배분과 방어 비교 전략

### 07·08 — 고정 70/30

`band-7030`은 QQQ 70%·BND 30%, `band-7030-tdf`는 QQQ 70%·TDF2050 프록시 30%를 항상
목표로 합니다. 두 전략 모두 매 거래일 최대 목표 편차가 5%p 이상인지 확인하고, 조건을
만족하면 1일에 리밸런싱합니다. 시장 상태나 수익 밴드는 사용하지 않습니다.

### 09 — Static 70/30

`static-7030`은 QQQ 70%, BND 15%, BIL 15%의 고정 배분입니다. 4상태 지표를 계산해 그래프와
분석에는 기록하지만 목표 비중에는 반영하지 않습니다. 매월 목표 편차 5%p 이상일 때만
1일 리밸런싱하는 비활성 비교 전략입니다.

### 10 — Trend Band Defense

`trend-band-defense`는 비활성 탐색 전략입니다.

- 정상 목표는 QQQ 65%, GLD 5%, BND 30%입니다.
- QQQ가 최근 최고가에서 25% 이상 하락하면 방어 상태가 되고, 목표는 QQQ 30%, GLD 10%,
  BND 60%입니다. EMA55가 EMA200 이상이면 정상으로 돌아갑니다.
- 정상·방어와 별도로 EMA55/EMA200 추세와 QQQ 실제 비중의 상·하한 밴드를 확인합니다.
- RSI14와 60일 이격도가 극단적 과매수(95 초과·110 이상) 또는 과매도(20 이하·90 이하)면
  1일 리밸런싱합니다.

## 국내 상품 매핑

매핑 전략의 `source`는 기준 전략의 신호와 목표 비중을 계산합니다. `products`에 적지 않은
기준 자산은 그대로 남습니다. 따라서 같은 신호 규칙이라도 실제 매매 자산 구성이 아래 표처럼
달라집니다.

| 매핑 전략 | 기준 전략 | 기준 자산 → 실제 상품 |
|---|---|---|
| `kodex-nasdaq` | `profit-band-vxus` | QQQ → `379810.KS` 100%; BND·BIL·VXUS는 그대로 |
| `time-nasdaq` | `profit-band-vxus` | QQQ → `426030.KS` 100%; BND·BIL·VXUS는 그대로 |
| `koact-nasdaq` | `profit-band-vxus` | QQQ → `0015B0.KS` 100%; BND·BIL·VXUS는 그대로 |
| `nasdaq-mix` | `profit-band-vxus` | QQQ → `379810.KS` 50%, `426030.KS` 30%, `0015B0.KS` 20%; BND·BIL·VXUS는 그대로 |
| `time-vxus-tdf2050` | `profit-band-vxus-v2` | QQQ·VXUS → `426030.KS` 100%씩, BND·BIL → `434060.KS` 100%씩 |
| `time-tdf2050-profit-band` | `profit-band-tdf2050` | QQQ → `426030.KS` 100%, TDF2050 프록시 → `434060.KS` 100%; BND·BIL은 그대로 |
| `nasdaq-tdf2050-7030` | `band-7030-tdf` | QQQ → `426030.KS` 100%, TDF2050 프록시 → `434060.KS` 100% |
| `buy-3dip-tdf` | `buy-3dip-bil` | QQQ는 그대로, BIL → `434060.KS` 100% |
| `qqq-valuation-breakdown-balanced-kodex-koact` | `qqq-valuation-breakdown-balanced` | QQQ → `379810.KS` 50% + `0015B0.KS` 50%, BIL → `488770.KS` 100% |
| `band-7030-tdf-valuation-defense-kodex-koact` | `band-7030-tdf-valuation-defense` | QQQ → `379810.KS` 50% + `0015B0.KS` 50%, TDF2050 프록시 → `434060.KS` 100%, BIL → `488770.KS` 100% |

상품 코드에 대한 메모는 각 매핑 YAML의 주석을 따릅니다. 특히 `time-vxus-tdf2050`은 기준
전략에서 서로 다른 QQQ와 VXUS 비중을 모두 `426030.KS`로 합산하므로, 기준 자산별 비중과
최종 상품별 비중을 구분해서 봐야 합니다.

## 규칙 변경 시 갱신 범위

전략 YAML을 바꾸면 이 문서의 다음 항목도 함께 갱신합니다.

1. 빠른 목록의 자동 로드 여부와 역할
2. 상태 전이의 조건·확인 기간
3. 목표 비중, 수익 밴드, 안전 슬리브와 리밸런싱 기준
4. 상품 매핑과 원본 전략의 관계

전략을 왜 채택하거나 기각했는지, 실험의 기간·성과·한계는 이 문서에 중복하지 않습니다.
[전략 연구 결정 기록](research/README.md)과 연결된 실험 문서에서 관리합니다.
