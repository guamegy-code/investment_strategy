"""Probe a 10% minimum deep-tier allocation against the stress scenarios."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_failed_dip_regime import DOTCOM, ROOT, RESULT_DIR, build_data, summarize
from strategy28_mixed_tuning import MixedTuning, apply_credit_lag
from strategy30_unknown_crash_stress import SCENARIOS, inject, run
from backtest import Backtest
from config import COMMISSION, SLIPPAGE


def main():
    rows = []
    for name, scenario in SCENARIOS.items():
        with TemporaryDirectory(prefix="floor10-stress-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=False)
            apply_credit_lag(directory, 1)
            inject(directory, scenario)
            metrics, _ = run(directory, "FLOOR10")
            row = {"Scenario": name, "Variant": "FLOOR10", **metrics}
            rows.append(row)
            print(row, flush=True)
    with TemporaryDirectory(prefix="floor10-dotcom-", dir=ROOT / "tmp") as temp:
        directory = Path(temp)
        build_data(directory, dotcom=True)
        apply_credit_lag(directory, 1)
        strategy = MixedTuning(cape_proxy=True, no_topup=True, topup_floor=.10)
        history, trades, rebalances = Backtest(
            strategy, data_dir=directory, tickers=strategy.required_tickers,
            start_date=DOTCOM[0], end_date=DOTCOM[1],
            commission=COMMISSION, slippage=SLIPPAGE,
        ).run_all()
        row = {"Scenario": "DOTCOM_PROXY", "Variant": "FLOOR10",
               **summarize(history, trades, rebalances, strategy)}
        rows.append(row)
        print(row, flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULT_DIR / "strategy30_floor_probe_summary.csv", index=False)


if __name__ == "__main__":
    main()
