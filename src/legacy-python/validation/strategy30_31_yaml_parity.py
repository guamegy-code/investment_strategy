"""Check executable 30/31 YAML against the earlier research wrapper.

The research QQQ/SPY/BAA input is used only inside a temporary directory.
Nothing in this script downloads or publishes the raw credit series.
"""

from pathlib import Path
from copy import deepcopy
import argparse
from shutil import copy2
from tempfile import TemporaryDirectory
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest
from config import COMMISSION, DATA_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT, build_data
from strategy28_mixed_tuning import MixedTuning, apply_credit_lag


STRATEGY30 = ROOT / "strategies/30_qqq_valuation_credit_guard_no_topup.yaml"
STRATEGY31 = ROOT / "strategies/31_band_7030_tdf_valuation_credit_guard_no_topup.yaml"
STRATEGY29 = ROOT / "strategies/29_band_7030_tdf_valuation_warning_dip_buyer.yaml"


def original_30_definition():
    """Reconstruct the pre-tuning YAML for the research-wrapper parity check."""
    definition = deepcopy(load_strategy_definition(STRATEGY30))
    release = definition["state"]["credit_guard"]["rules"][1]
    assert "QQQ.ema55 > QQQ.ema200 * 0.97" in release["when"]
    assert "and BAA10Y.close < 3.0" in release["when"]
    assert release["confirm"] == 5
    release["when"] = release["when"].replace(
        "QQQ.ema55 > QQQ.ema200 * 0.97", "QQQ.ema55 > QQQ.ema200"
    ).replace("and BAA10Y.close < 3.0", "").rstrip() + "\n"
    release["confirm"] = 10
    first = next(rule for rule in definition["target"]
                 if rule.get("when") == "state.credit_guard == 'TRUE'")
    assert first["weights"] == {"QQQ": "50%", "BIL": "50%"}
    first["weights"] = {"QQQ": "65%", "BIL": "35%"}
    definition["strategy"]["version"] = 1
    return definition


def add_credit_observation(directory):
    qqq = pd.read_csv(directory / "QQQ.csv", index_col="Date", parse_dates=True)
    spread = qqq["BAA_SPREAD"]
    credit = pd.DataFrame(index=qqq.index)
    for field in ("Open", "High", "Low", "Close"):
        credit[field] = spread
    credit["Volume"] = 0
    credit["ROC20"] = spread.pct_change(20) * 100
    credit.index.name = "Date"
    credit.to_csv(directory / "BAA10Y.csv")


def run(strategy, directory, dates):
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    performance = Performance(history)
    metrics = {
        "CAGR": performance.cagr(), "MDD": performance.mdd(),
        "Trades": len(trades), "Rebalances": len(rebalances),
    }
    return metrics, history


def main(*, current_only=False):
    rows = []
    for period, dates, proxy in (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    ):
        if current_only and period != "CURRENT":
            continue
        with TemporaryDirectory(prefix="strategy30-parity-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=proxy)
            apply_credit_lag(directory, 1)
            add_credit_observation(directory)
            research_metrics = None
            for name, strategy in (
                ("RESEARCH_30", MixedTuning(cape_proxy=proxy, no_topup=True)),
                ("YAML_30_BASE", DeclarativeStrategy(original_30_definition())),
                ("YAML_30", DeclarativeStrategy(load_strategy_definition(STRATEGY30))),
            ):
                if proxy and name.startswith("YAML_30"):
                    strategy.parameters["valuation_arm_score"] = 30.0
                    strategy.parameters["valuation_breakdown_points"] = 4.0
                metrics, history = run(strategy, directory, dates)
                rows.append({"Period": period, "Strategy": name, **metrics})
                print(rows[-1], flush=True)
                if name == "RESEARCH_30":
                    research_metrics = metrics
                elif name == "YAML_30_BASE":
                    for field in ("CAGR", "MDD", "Trades", "Rebalances"):
                        assert abs(metrics[field] - research_metrics[field]) < 1e-9, (
                            period, field, metrics[field], research_metrics[field]
                        )
            if period == "CURRENT":
                copy2(DATA_DIR / "TDF2050_PROXY.csv", directory / "TDF2050_PROXY.csv")
                for name, path in (("YAML_29", STRATEGY29), ("YAML_31", STRATEGY31)):
                    strategy = DeclarativeStrategy(load_strategy_definition(path))
                    metrics, history = run(strategy, directory, dates)
                    rows.append({"Period": period, "Strategy": name, **metrics})
                    print(rows[-1], flush=True)
                for cap in (60, 65):
                    definition = deepcopy(load_strategy_definition(STRATEGY31))
                    first = next(rule for rule in definition["target"]
                                 if rule.get("when") == "state.credit_guard == 'TRUE'")
                    first["weights"] = {
                        "QQQ": f"{cap}%", "TDF2050_PROXY": "30%",
                        "BIL": f"{70-cap}%",
                    }
                    strategy = DeclarativeStrategy(definition)
                    metrics, _ = run(strategy, directory, dates)
                    rows.append({"Period": period, "Strategy": f"YAML_31_CAP{cap}", **metrics})
                    print(rows[-1], flush=True)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-only", action="store_true")
    main(current_only=parser.parse_args().current_only)
