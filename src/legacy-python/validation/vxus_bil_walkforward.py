"""Walk-forward validation for the share of BIL replaced by VXUS."""

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, END_DATE, RESULT_DIR, START_DATE
from performance import Performance
from strategy import RetirementAllocationStrategy
from .walkforward import WalkForwardProfileComparison


BIL_SUBSTITUTION_SHARES = (0.0, 0.25, 0.50, 0.75, 1.0)
LOOKBACK_YEARS = 5


def profile_name(share):
    return f"BIL_{int(round(share * 100)):03d}PCT"


class VXUSBILShareStrategy(RetirementAllocationStrategy):
    """Replace BND fully and a fixed share of selected BIL with VXUS."""

    ALTERNATIVE_RISK_ASSET = "VXUS"

    def __init__(self, bil_substitution_share):
        if bil_substitution_share not in BIL_SUBSTITUTION_SHARES:
            raise ValueError("BIL substitution share must use a tested step")
        self.bil_substitution_share = float(bil_substitution_share)
        super().__init__()

    @property
    def required_tickers(self):
        return tuple(dict.fromkeys((
            *super().required_tickers,
            self.ALTERNATIVE_RISK_ASSET,
        )))

    def _target_for_state(self):
        target = super()._target_for_state()
        target[self.ALTERNATIVE_RISK_ASSET] = 0.0
        qqq_weight = target["QQQ"]
        if qqq_weight >= self.MAX_RISK_WEIGHT:
            return target

        substitution_share = (
            1.0
            if self.safe_asset == self.BOND_ASSET
            else self.bil_substitution_share
        )
        capacity = self.MAX_RISK_WEIGHT - qqq_weight
        vxus_weight = min(
            target[self.safe_asset] * substitution_share,
            capacity,
        )
        target[self.safe_asset] = round(
            target[self.safe_asset] - vxus_weight, 10
        )
        target[self.ALTERNATIVE_RISK_ASSET] = round(vxus_weight, 10)
        return target


def _run_profile(share):
    strategy = VXUSBILShareStrategy(share)
    backtest = Backtest(
        strategy,
        data_dir=DATA_DIR,
        tickers=strategy.required_tickers,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "profile": profile_name(share),
        "share": share,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }


def _full_period_report(results):
    rows = []
    for result in results:
        history = result["history"]
        metrics = Performance(history).summary()
        vxus = history["Weights"].apply(
            lambda weights: weights.get("VXUS", 0.0)
        )
        rows.append({
            "Profile": result["profile"],
            "BILSubstitutionShare": result["share"],
            "StartDate": history.index.min(),
            "EndDate": history.index.max(),
            "CAGR": metrics["CAGR"],
            "MDD": metrics["MDD"],
            "Sharpe": metrics["Sharpe"],
            "Calmar": metrics["Calmar"],
            "EndValue": history["Portfolio"].iloc[-1],
            "Trades": len(result["trades"]),
            "TransactionCosts": metrics["TransactionCosts"],
            "AverageVXUSWeight": vxus.mean(),
            "VXUSExposureDayRate": (vxus > 0.001).mean(),
        })
    return pd.DataFrame(rows).sort_values(
        "BILSubstitutionShare"
    ).reset_index(drop=True)


def _selection_frequency(selected):
    if selected.empty:
        return pd.DataFrame(columns=("SelectedProfile", "Years", "Rate"))
    counts = selected["SelectedProfile"].value_counts().sort_index()
    return pd.DataFrame({
        "SelectedProfile": counts.index,
        "Years": counts.values,
        "Rate": counts.values / counts.sum(),
    })


def run_vxus_bil_walkforward():
    """Run fixed-share sensitivity and trailing-five-year selection tests."""
    results = [_run_profile(share) for share in BIL_SUBSTITUTION_SHARES]
    histories = {
        result["profile"]: result["history"] for result in results
    }
    baseline = histories[profile_name(0.0)]
    comparison = WalkForwardProfileComparison(
        histories,
        benchmark_history=baseline,
        lookback_years=LOOKBACK_YEARS,
    )
    walkforward_reports = comparison.all_reports()
    reports = {
        "vxus_bil_full_period": _full_period_report(results),
        "vxus_bil_walkforward_yearly": walkforward_reports[
            "walkforward_yearly_comparison"
        ],
        "vxus_bil_walkforward_selected": walkforward_reports[
            "walkforward_selected_years"
        ],
        "vxus_bil_walkforward_summary": walkforward_reports[
            "walkforward_summary"
        ],
    }
    reports["vxus_bil_walkforward_selection_frequency"] = (
        _selection_frequency(reports["vxus_bil_walkforward_selected"])
    )
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_vxus_bil_walkforward()
    print(output["vxus_bil_full_period"].to_string(index=False))
    print("\nWalk-forward selected years")
    print(output["vxus_bil_walkforward_selected"].to_string(index=False))
    print("\nWalk-forward summary")
    print(output["vxus_bil_walkforward_summary"].to_string(index=False))
