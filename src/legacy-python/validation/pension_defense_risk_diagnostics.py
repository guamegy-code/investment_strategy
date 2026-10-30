"""Post-hoc product-mix attribution; these cases are not new strategy candidates."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pension_defense_review import LOCK_PATH, load_market, metrics, simulate, source_schedule
from result_paths import research_result_path


def main():
    prices, original, _, dates = load_market("DOMESTIC")
    schedule = source_schedule()
    # Keep both products in the comparison calendar, including a zero target,
    # so changing the diagnostic allocation cannot change the anchor/history.
    cases = {"CURRENT_MIX": original, "MARKET_ONLY": {"RISK1": 1., "RISK2": 0.},
             "GROWTH_ONLY": {"RISK1": 0., "RISK2": 1.}}
    rows = []
    for case, mix in cases.items():
        history, trades, activity, rebalances = simulate(prices, mix, schedule, "MM", dates)
        row = {"Case": case, **metrics(history, trades, rebalances)}
        values = history.Portfolio
        trough = (values/values.cummax()-1).idxmin()
        row["TroughDate"] = str(trough.date())
        row["PeakDate"] = str(values.loc[:trough].idxmax().date())
        rows.append(row)
        history.to_csv(research_result_path(f"pension_defense_risk_{case.lower()}_daily.csv"))
        if case == "CURRENT_MIX":
            baseline = pd.read_csv(research_result_path("pension_defense_domestic_mm_daily.csv"), index_col="Date", parse_dates=True)
            pd.testing.assert_series_equal(history.Portfolio, baseline.Portfolio,
                check_exact=False, rtol=1e-12, atol=1e-12)
        print(row, flush=True)
    pd.DataFrame(rows).to_csv(research_result_path("pension_defense_risk_attribution.csv"), index=False)
    research_result_path("pension_defense_risk_manifest.json").write_text(json.dumps({
        "Purpose": "Post-hoc attribution after the domestic drawdown observation. Not a candidate-selection experiment.",
        "Cases": cases,
        "CodeSHA256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "DesignSHA256": hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest(),
        "MainCodeSHA256": hashlib.sha256((Path(__file__).parent/"pension_defense_review.py").read_bytes()).hexdigest(),
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
