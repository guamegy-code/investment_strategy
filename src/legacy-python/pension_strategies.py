"""기준 지수를 실제 퇴직연금 상품으로 변환하는 전략을 정의한다."""

from experimental_strategies import (
    RetirementAllocationProfitBandVXUSStrategy,
)


DEFAULT_RETIREMENT_PRODUCTS = {
    "KODEX": "379810.KS",
    "TIME": "426030.KS",
    "KOACT": "0015B0.KS",
}


class SingleProductAllocationStrategy(
    RetirementAllocationProfitBandVXUSStrategy
):
    """QQQ 상품 매핑에 ProfitBand와 VXUS 대체를 적용한다."""

    DEFAULT_RISK_ASSET = None
    FX_RATE_TICKER = "KRW=X"

    def __init__(
        self,
        signal_asset="QQQ",
        risk_asset=None,
        bond_asset="BND",
        cash_asset="BIL",
    ):
        selected_risk_asset = risk_asset or self.DEFAULT_RISK_ASSET
        if selected_risk_asset is None:
            raise ValueError("퇴직연금 위험자산 상품이 필요합니다")
        super().__init__(
            signal_asset=signal_asset,
            asset_mapping={
                "QQQ": {selected_risk_asset: 1.0},
                "BND": {bond_asset: 1.0},
                "BIL": {cash_asset: 1.0},
            },
        )


class KodexNasdaqAllocationStrategy(
    SingleProductAllocationStrategy
):
    """QQQ로 시장을 판단하고 KODEX 미국나스닥100을 매매한다."""

    DEFAULT_RISK_ASSET = DEFAULT_RETIREMENT_PRODUCTS["KODEX"]
    bond_asset = "437080.KS"
    cash_asset = "0046A0.KS"


class TimeNasdaqAllocationStrategy(
    SingleProductAllocationStrategy
):
    """QQQ로 시장을 판단하고 TIME 미국나스닥100액티브를 매매한다."""

    DEFAULT_RISK_ASSET = DEFAULT_RETIREMENT_PRODUCTS["TIME"]


class KoActNasdaqAllocationStrategy(
    SingleProductAllocationStrategy
):
    """QQQ로 시장을 판단하고 KoAct 미국나스닥성장액티브를 매매한다."""

    DEFAULT_RISK_ASSET = DEFAULT_RETIREMENT_PRODUCTS["KOACT"]


class NasdaqProductMixAllocationStrategy(
    RetirementAllocationProfitBandVXUSStrategy
):
    """나스닥 상품 혼합에 ProfitBand와 VXUS 대체를 적용한다."""

    PRODUCT_WEIGHTS = {"KODEX": 0.50, "TIME": 0.30, "KOACT": 0.20}
    FX_RATE_TICKER = "KRW=X"

    def __init__(
        self,
        signal_asset="QQQ",
        bond_asset="BND",
        cash_asset="BIL",
        product_assets=None,
    ):
        assets = {**DEFAULT_RETIREMENT_PRODUCTS, **(product_assets or {})}
        risk_assets = {
            assets[name]: weight
            for name, weight in self.PRODUCT_WEIGHTS.items()
        }
        super().__init__(
            signal_asset=signal_asset,
            asset_mapping={
                "QQQ": risk_assets,
                "BND": {bond_asset: 1.0},
                "BIL": {cash_asset: 1.0},
            },
        )
