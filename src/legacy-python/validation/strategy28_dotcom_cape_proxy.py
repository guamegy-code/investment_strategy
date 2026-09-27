"""Research-only dot-com replay of strategy 28 with lagged S&P 500 CAPE.

This is not the production valuation score or a faithful live-ETF replay:
pre-2007 BIL is an ^IRX-based proxy, and Shiller CAPE is broad-market,
retrospectively revised monthly data.  Keep generated inputs out of data/.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from backtest import Backtest
from config import DATA_DIR, EXTENDED_DATA_DIR, RESULT_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
START, END = "2000-03-10", "2005-12-30"
STRATEGY = ROOT / "strategies/28_qqq_valuation_warning_dip_buyer.yaml"


def lagged_cape(dates: pd.DatetimeIndex, lag_months: int = 1) -> pd.Series:
    cape = pd.read_csv(DATA_DIR / "shiller_cape.csv", parse_dates=["Date"])
    cape = cape.loc[cape.PE10.gt(0), ["Date", "PE10"]].set_index("Date").sort_index()
    # Month t's observation is only allowed from the first day of month t+1.
    cape.index = cape.index.to_period("M").to_timestamp() + pd.offsets.MonthBegin(lag_months)
    return cape.PE10.reindex(dates, method="ffill").rename("Lagged_SP500_CAPE")


def definition(arm: float | None, decline: float = 4.0) -> dict:
    result = deepcopy(load_strategy_definition(STRATEGY))
    result["strategy"]["id"] += "-dotcom-cape-research"
    if arm is None:
        result["parameters"]["valuation_arm_score"] = 1e6
    else:
        result["parameters"]["valuation_arm_score"] = arm
        result["parameters"]["valuation_breakdown_points"] = decline
    return result


def replay(data_dir: Path, arm: float | None, decline: float = 4.0) -> tuple[dict, pd.DataFrame, list[dict]]:
    strategy = DeclarativeStrategy(definition(arm, decline))
    history, trades, rebalances = Backtest(
        strategy,
        data_dir=data_dir,
        tickers=strategy.required_tickers,
        start_date=START,
        end_date=END,
    ).run_all()
    value = history.Portfolio
    drawdown = value / value.cummax() - 1
    trough = drawdown.idxmin()
    perf = Performance(history)
    result = {
        "arm": arm if arm is not None else "disabled",
        "decline": decline if arm is not None else "disabled",
        "start": str(history.index[0].date()),
        "end": str(history.index[-1].date()),
        "CAGR": perf.cagr(),
        "MDD": perf.mdd(),
        "peak": str(value.loc[:trough].idxmax().date()),
        "trough": str(trough.date()),
        "trades": len(trades),
        "rebalances": len(rebalances),
        "end_value": float(value.iloc[-1] / value.iloc[0]),
    }
    events = []
    for date, context in history.NotificationContext.items():
        if not isinstance(context, dict):
            continue
        for change in context.get("state_changes", []):
            if change["name"] not in ("defense_mode", "stage", "trend_mode"):
                continue
            events.append({
                "Date": str(date.date()),
                "State": change["name"],
                "Previous": change["previous"],
                "Current": change["current"],
                "QQQ_Target": context.get("target_weights", {}).get("QQQ"),
                "BIL_Target": context.get("target_weights", {}).get("BIL"),
            })
    return result, history, events


def main() -> None:
    rows = []
    for lag_months in (1, 2):
        with TemporaryDirectory(prefix="strategy28-cape-", dir=ROOT / "tmp") as temp:
            research_dir = Path(temp)
            qqq = pd.read_csv(EXTENDED_DATA_DIR / "QQQ.csv", index_col="Date", parse_dates=True)
            cape = lagged_cape(qqq.index, lag_months)
            qqq["VALUATION_SCORE"] = cape
            qqq.to_csv(research_dir / "QQQ.csv", index_label="Date")
            if lag_months == 1:
                daily = qqq.loc[START:END, ["Close", "VALUATION_SCORE"]].rename(
                    columns={"Close": "QQQ_Adjusted_Close", "VALUATION_SCORE": "Lagged_SP500_CAPE"}
                )
                daily.to_csv(RESULT_DIR / "strategy28_dotcom_cape_daily.csv", index_label="Date")
            # SPY exists back to 1999; BIL before 2007 is an ^IRX-based proxy.
            for ticker, directory in (("SPY", DATA_DIR), ("BIL", EXTENDED_DATA_DIR)):
                frame = pd.read_csv(directory / f"{ticker}.csv", index_col="Date", parse_dates=True)
                frame.to_csv(research_dir / f"{ticker}.csv", index_label="Date")
            baseline_history = None
            for arm, decline in ((None, 4.0), (30.0, 2.0), (30.0, 4.0), (30.0, 6.0)):
                row, history, events = replay(research_dir, arm, decline)
                if arm is None:
                    baseline_history = history
                else:
                    assert baseline_history is not None
                    base_targets = baseline_history.NotificationContext.map(
                        lambda context: context["target_weights"].get("QQQ", 0.0)
                    )
                    variant_targets = history.NotificationContext.map(
                        lambda context: context["target_weights"].get("QQQ", 0.0)
                    )
                    different = (base_targets - variant_targets).abs() > 1e-9
                    row["first_target_difference"] = (
                        str(different[different].index[0].date()) if different.any() else None
                    )
                    row["target_difference_days"] = int(different.sum())
                row["lag_months"] = lag_months
                rows.append(row)
                print(row, flush=True)
                if arm == 30.0 and decline == 4.0:
                    event_output = RESULT_DIR / f"strategy28_dotcom_cape_events_lag{lag_months}.csv"
                    pd.DataFrame(events).to_csv(event_output, index=False)
    RESULT_DIR.mkdir(exist_ok=True)
    output = RESULT_DIR / "strategy28_dotcom_cape_proxy.csv"
    pd.DataFrame(rows).to_csv(output, index=False)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
