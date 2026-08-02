"""Pension strategies for individual Nasdaq products and their mixture."""

from strategy import PensionRiskAllocationStrategy


DEFAULT_PENSION_PRODUCTS = {
    "KODEX": "379810.KS",
    "TIME": "426030.KS",
    "KOACT": "0015B0.KS",
}


class PensionProductStrategy(PensionRiskAllocationStrategy):
    """Base strategy that uses QQQ signals to trade one pension product."""

    DEFAULT_RISK_ASSET = None

    def __init__(
        self,
        signal_asset="QQQ",
        risk_asset=None,
        bond_asset="BND",
        cash_asset="BIL",
    ):
        selected_risk_asset = risk_asset or self.DEFAULT_RISK_ASSET
        if selected_risk_asset is None:
            raise ValueError("a pension risk asset is required")
        super().__init__(
            signal_asset=signal_asset,
            risk_asset=selected_risk_asset,
            bond_asset=bond_asset,
            cash_asset=cash_asset,
        )


class PensionKodexStrategy(PensionProductStrategy):
    """Trade KODEX US Nasdaq 100 while using QQQ regime signals."""

    DEFAULT_RISK_ASSET = DEFAULT_PENSION_PRODUCTS["KODEX"]


class PensionTimeStrategy(PensionProductStrategy):
    """Trade TIME US Nasdaq 100 Active while using QQQ regime signals."""

    DEFAULT_RISK_ASSET = DEFAULT_PENSION_PRODUCTS["TIME"]


class PensionKoActStrategy(PensionProductStrategy):
    """Trade KoAct US Nasdaq Growth Active while using QQQ regime signals."""

    DEFAULT_RISK_ASSET = DEFAULT_PENSION_PRODUCTS["KOACT"]


class PensionNasdaqMixStrategy(PensionRiskAllocationStrategy):
    """Blend KODEX 50%, TIME 30%, and KoAct 20% in the risk sleeve."""

    PRODUCT_WEIGHTS = {"KODEX": 0.50, "TIME": 0.30, "KOACT": 0.20}

    def __init__(
        self,
        signal_asset="QQQ",
        bond_asset="BND",
        cash_asset="BIL",
        product_assets=None,
    ):
        assets = {**DEFAULT_PENSION_PRODUCTS, **(product_assets or {})}
        risk_assets = {
            assets[name]: weight
            for name, weight in self.PRODUCT_WEIGHTS.items()
        }
        super().__init__(
            signal_asset=signal_asset,
            risk_assets=risk_assets,
            bond_asset=bond_asset,
            cash_asset=cash_asset,
        )
