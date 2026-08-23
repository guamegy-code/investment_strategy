"""Validate SafeBlend inside the 80% retirement profit band."""

from __future__ import annotations

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, RESULT_DIR, START_DATE
from experimental_strategies import (
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
    _UpperRiskBandMixin,
)
from performance import Performance
from strategy import (
    AllocationState,
    RetirementAllocationLegacyStrategy,
    RetirementAllocationSafeBlendStrategy,
    RetirementAllocationVXUSStrategy,
    RetirementAllocationStrategy,
    SafeBlendAllocationStrategy,
    _SafeBlendMixin,
    _VXUSSubstitutionMixin,
)


COMMON_END_DATE = "2026-07-31"
FIXED_WINDOWS = (
    ("FULL", START_DATE, COMMON_END_DATE),
    ("2012_2022", "2012-01-01", "2022-12-31"),
    ("2024_PLUS", "2024-01-01", COMMON_END_DATE),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", COMMON_END_DATE),
)


class _SafeBlendProfitBandValidationMixin:
    """Preserve QQQ profit while applying the current BND/BIL blend."""

    def _preserve_profit_target(self, market, portfolio, target):
        prices = {ticker: market[ticker]["Close"] for ticker in target}
        weights = portfolio.weights(prices)
        preserved = target.copy()
        for ticker in self.risk_assets:
            preserved[ticker] = weights.get(ticker, 0.0)

        preserved[self.BOND_ASSET] = 0.0
        preserved[self.CASH_ASSET] = 0.0
        non_safe_weight = sum(
            weight
            for ticker, weight in preserved.items()
            if ticker not in (self.BOND_ASSET, self.CASH_ASSET)
        )
        safe_weight = max(0.0, 1.0 - non_safe_weight)
        safe_mix = self.safe_asset_mix or {
            self.BOND_ASSET: float(self.safe_asset == self.BOND_ASSET),
            self.CASH_ASSET: float(self.safe_asset == self.CASH_ASSET),
        }
        mix_total = sum(safe_mix.values()) or 1.0
        bond_share = safe_mix[self.BOND_ASSET] / mix_total
        preserved[self.BOND_ASSET] = round(safe_weight * bond_share, 10)
        preserved[self.CASH_ASSET] = round(
            safe_weight - preserved[self.BOND_ASSET], 10
        )
        return preserved

    def evaluate(self, date, market, portfolio):
        previous_mix = (
            None if self.safe_asset_mix is None else self.safe_asset_mix.copy()
        )
        signal = super().evaluate(date, market, portfolio)
        if previous_mix is None or previous_mix == self.safe_asset_mix:
            return signal
        if self.state not in (AllocationState.BULL, AllocationState.CAUTION):
            return signal

        qqq_weight = self._current_qqq_weight(
            market, portfolio, signal["target"]
        )
        canonical_qqq = sum(
            self.target.get(ticker, 0.0) for ticker in self.risk_assets
        )
        if not canonical_qqq < qqq_weight < self.upper_risk_weight:
            return signal
        if signal["rebalance"]:
            return signal
        return self._signal(
            True,
            signal["target"],
            1,
            "SAFE_BLEND_ROTATION_PRESERVE_QQQ",
        )


class RetirementAllocationSafeBlendProfitBandStrategy(
    _SafeBlendProfitBandValidationMixin,
    _SafeBlendMixin,
    _UpperRiskBandMixin,
    RetirementAllocationStrategy,
):
    """Validation candidate combining SafeBlend with the 80% profit band."""


class RetirementAllocationSafeBlendProfitBandVXUSStrategy(
    _SafeBlendProfitBandValidationMixin,
    _VXUSSubstitutionMixin,
    _SafeBlendMixin,
    _UpperRiskBandMixin,
    RetirementAllocationStrategy,
):
    """Validation candidate combining SafeBlend, VXUS, and the profit band."""


class _DefensiveSafeBlendValidationMixin:
    """Apply the 25% BND/BIL blend only to BEAR and RECOVERY targets."""

    def __init__(self, *args, **kwargs):
        self.defensive_safe_mix = None
        super().__init__(*args, **kwargs)

    def _calculate_defensive_safe_mix(self, market):
        roc_column = f"ROC{self.SAFE_MOMENTUM_PERIOD}"
        bnd_roc = market[self.BOND_ASSET].get(roc_column)
        bil_roc = market[self.CASH_ASSET].get(roc_column)
        if not self._valid(bnd_roc, bil_roc):
            selected = self.safe_asset or self.BOND_ASSET
            return {
                self.BOND_ASSET: float(selected == self.BOND_ASSET),
                self.CASH_ASSET: float(selected == self.CASH_ASSET),
            }

        spread = float(bnd_roc) - float(bil_roc)
        if spread >= _SafeBlendMixin.SAFE_BLEND_OUTER_THRESHOLD:
            bond_share = 1.00
        elif spread >= _SafeBlendMixin.SAFE_BLEND_INNER_THRESHOLD:
            bond_share = 0.75
        elif spread >= -_SafeBlendMixin.SAFE_BLEND_INNER_THRESHOLD:
            bond_share = 0.50
        elif spread > -_SafeBlendMixin.SAFE_BLEND_OUTER_THRESHOLD:
            bond_share = 0.25
        else:
            bond_share = 0.00
        return {
            self.BOND_ASSET: bond_share,
            self.CASH_ASSET: 1.0 - bond_share,
        }

    def evaluate(self, date, market, portfolio):
        if date.to_period("M") != self.last_safe_selection_month:
            self.defensive_safe_mix = self._calculate_defensive_safe_mix(
                market
            )
        return super().evaluate(date, market, portfolio)

    def _index_target_for_state(self):
        target = super()._index_target_for_state()
        if self.state not in (AllocationState.BEAR, AllocationState.RECOVERY):
            return target
        safe_mix = self.defensive_safe_mix
        if safe_mix is None:
            return target
        safe_weight = target[self.BOND_ASSET] + target[self.CASH_ASSET]
        target[self.BOND_ASSET] = round(
            safe_weight * safe_mix[self.BOND_ASSET], 10
        )
        target[self.CASH_ASSET] = round(
            safe_weight - target[self.BOND_ASSET], 10
        )
        return target


class RetirementAllocationDefensiveSafeBlendProfitBandStrategy(
    RetirementAllocationProfitBandStrategy,
):
    """Validation candidate blending safe assets only in defensive states."""


class DefensiveSafeBlendAllocationStrategy(
    _DefensiveSafeBlendValidationMixin,
    RetirementAllocationLegacyStrategy,
):
    """Validation candidate for replacing the all-state SafeBlend behavior."""


class RetirementAllocationDefensiveSafeBlendProfitBandVXUSStrategy(
    RetirementAllocationProfitBandVXUSStrategy,
):
    """Defensive-only SafeBlend candidate with VXUS substitution."""


class RetirementAllocationDefensiveSafeBlendSelectiveVXUSStrategy(
    _VXUSSubstitutionMixin,
    _DefensiveSafeBlendValidationMixin,
    RetirementAllocationStrategy,
):
    """Selective VXUS candidate with SafeBlend only in defensive states."""


VARIANTS = (
    ("RETIREMENT", RetirementAllocationLegacyStrategy),
    ("SAFE_BLEND", SafeBlendAllocationStrategy),
    ("DEFENSIVE_SAFE_BLEND", DefensiveSafeBlendAllocationStrategy),
    ("SELECTIVE", RetirementAllocationStrategy),
    ("PROFIT_BAND_80", RetirementAllocationProfitBandStrategy),
    ("SELECTIVE_SAFE_BLEND", RetirementAllocationSafeBlendStrategy),
    ("SAFE_BLEND_PROFIT_BAND_80", RetirementAllocationSafeBlendProfitBandStrategy),
    (
        "DEFENSIVE_SAFE_BLEND_PROFIT_BAND_80",
        RetirementAllocationDefensiveSafeBlendProfitBandStrategy,
    ),
    ("SELECTIVE_VXUS", RetirementAllocationVXUSStrategy),
    (
        "DEFENSIVE_SAFE_BLEND_SELECTIVE_VXUS",
        RetirementAllocationDefensiveSafeBlendSelectiveVXUSStrategy,
    ),
    ("PROFIT_BAND_80_VXUS", RetirementAllocationProfitBandVXUSStrategy),
    (
        "SAFE_BLEND_PROFIT_BAND_80_VXUS",
        RetirementAllocationSafeBlendProfitBandVXUSStrategy,
    ),
    (
        "DEFENSIVE_SAFE_BLEND_PROFIT_BAND_80_VXUS",
        RetirementAllocationDefensiveSafeBlendProfitBandVXUSStrategy,
    ),
)


def _run(factory, start_date, end_date):
    strategy = factory()
    with contextlib.redirect_stdout(io.StringIO()):
        history, trades, rebalances = Backtest(
            strategy,
            data_dir=DATA_DIR,
            tickers=strategy.required_tickers,
            start_date=start_date,
            end_date=end_date,
        ).run_all()
    metrics = Performance(history).summary()
    return {
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "SafeBlendEvents": sum(
            "SAFE_BLEND_ROTATION" in (event.get("Reason") or "")
            for event in rebalances
        ),
    }


def _report(windows):
    return pd.DataFrame([
        {"Window": window, "Variant": label, **_run(factory, start, end)}
        for window, start, end in windows
        for label, factory in VARIANTS
    ])


def _rolling_windows():
    return tuple(
        (f"{year}_{year + 2}", f"{year}-01-01", f"{year + 2}-12-31")
        for year in range(pd.Timestamp(START_DATE).year, 2025)
    )


def _rolling_comparison(rolling):
    pairs = (
        ("SAFE_BLEND", "DEFENSIVE_SAFE_BLEND"),
        ("SELECTIVE_VXUS", "DEFENSIVE_SAFE_BLEND_SELECTIVE_VXUS"),
        ("PROFIT_BAND_80", "SAFE_BLEND_PROFIT_BAND_80"),
        ("PROFIT_BAND_80", "DEFENSIVE_SAFE_BLEND_PROFIT_BAND_80"),
        ("PROFIT_BAND_80_VXUS", "SAFE_BLEND_PROFIT_BAND_80_VXUS"),
        (
            "PROFIT_BAND_80_VXUS",
            "DEFENSIVE_SAFE_BLEND_PROFIT_BAND_80_VXUS",
        ),
    )
    rows = []
    for parent, candidate in pairs:
        parent_rows = rolling.loc[
            rolling["Variant"] == parent
        ].set_index("Window")
        candidate_rows = rolling.loc[
            rolling["Variant"] == candidate
        ].set_index("Window")
        cagr_gap = candidate_rows["CAGR"] - parent_rows["CAGR"]
        mdd_gap = candidate_rows["MDD"] - parent_rows["MDD"]
        sharpe_gap = candidate_rows["Sharpe"] - parent_rows["Sharpe"]
        affected = (
            (cagr_gap.abs() > 1e-12)
            | (mdd_gap.abs() > 1e-12)
            | (sharpe_gap.abs() > 1e-12)
        )
        rows.append({
            "Parent": parent,
            "Candidate": candidate,
            "Windows": len(cagr_gap),
            "AffectedWindows": int(affected.sum()),
            "CAGRWins": int((cagr_gap > 0.0).sum()),
            "CAGRLosses": int((cagr_gap < 0.0).sum()),
            "AverageCAGRGap": cagr_gap.mean(),
            "WorstCAGRGap": cagr_gap.min(),
            "MDDWins": int((mdd_gap > 0.0).sum()),
            "AverageMDDImprovement": mdd_gap.mean(),
            "SharpeWins": int((sharpe_gap > 0.0).sum()),
            "AverageSharpeGap": sharpe_gap.mean(),
        })
    return pd.DataFrame(rows)


def run_validation():
    fixed = _report(FIXED_WINDOWS)
    rolling = _report(_rolling_windows())
    comparison = _rolling_comparison(rolling)
    fixed.to_csv(RESULT_DIR / "retirement_safe_blend_profit_band_fixed.csv", index=False)
    rolling.to_csv(
        RESULT_DIR / "retirement_safe_blend_profit_band_rolling.csv", index=False
    )
    comparison.to_csv(
        RESULT_DIR / "retirement_safe_blend_profit_band_comparison.csv", index=False
    )
    return fixed, comparison


if __name__ == "__main__":
    fixed_report, rolling_comparison = run_validation()
    print(fixed_report.to_string(index=False))
    print("\nRolling three-year comparison")
    print(rolling_comparison.to_string(index=False))
