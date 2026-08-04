"""Retrospective comparison of the legacy and tuned DEFENSE2 strategies."""

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, RESULT_DIR
from performance import Performance
from experimental_strategies import ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED
from strategy import ASYMMETRIC_TREND_BAND_ADD_DEFENSE2


FIXED_WINDOWS = (
    ("FULL", "2012-01-01", None),
    ("EARLY", "2012-01-01", "2017-12-31"),
    ("MID", "2018-01-01", "2021-12-31"),
    ("RECENT", "2022-01-01", None),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", None),
)
DRAWDOWN_SENSITIVITY = (
    -0.12, -0.14, -0.15, -0.155, -0.16, -0.18, -0.20, -0.22, -0.25,
)


def _run(strategy_type, start_date, end_date):
    strategy = strategy_type()
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
        "StartDate": history.index.min(),
        "EndDate": history.index.max(),
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "Calmar": metrics["Calmar"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "TransactionCosts": metrics["TransactionCosts"],
    }


def _comparison(window, start_date, end_date):
    baseline = _run(
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2, start_date, end_date
    )
    tuned = _run(
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED, start_date, end_date
    )
    return {
        "Window": window,
        **{f"Baseline{key}": value for key, value in baseline.items()},
        **{f"Tuned{key}": value for key, value in tuned.items()},
        "CAGRImprovement": tuned["CAGR"] - baseline["CAGR"],
        "MDDImprovement": tuned["MDD"] - baseline["MDD"],
        "SharpeImprovement": tuned["Sharpe"] - baseline["Sharpe"],
        "CalmarImprovement": tuned["Calmar"] - baseline["Calmar"],
        "ImprovesBoth": (
            tuned["CAGR"] > baseline["CAGR"]
            and tuned["MDD"] > baseline["MDD"]
        ),
    }


def _rolling_windows():
    final_year = pd.Timestamp(
        pd.read_csv(DATA_DIR / "QQQ.csv", usecols=["Date"])["Date"].max()
    ).year
    return tuple(
        (
            f"ROLLING_3Y_{start_year}_{start_year + 2}",
            f"{start_year}-01-01",
            f"{start_year + 2}-12-31",
        )
        for start_year in range(2012, final_year - 1)
    )


def _sensitivity_report():
    rolling_windows = _rolling_windows()
    full_baseline = _run(
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2, "2012-01-01", None
    )
    rolling_baselines = {
        window: _run(ASYMMETRIC_TREND_BAND_ADD_DEFENSE2, start, end)
        for window, start, end in rolling_windows
    }
    rows = []
    for drawdown in DRAWDOWN_SENSITIVITY:
        factory = lambda value=drawdown: (
            ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED(value)
        )
        full = _run(factory, "2012-01-01", None)
        rolling = []
        for window, start, end in rolling_windows:
            candidate = _run(factory, start, end)
            baseline = rolling_baselines[window]
            rolling.append({
                "CAGRGap": candidate["CAGR"] - baseline["CAGR"],
                "MDDImprovement": candidate["MDD"] - baseline["MDD"],
                "ImprovesBoth": (
                    candidate["CAGR"] > baseline["CAGR"]
                    and candidate["MDD"] > baseline["MDD"]
                ),
            })
        rows.append({
            "DefensiveDrawdown": drawdown,
            "NormalSafeAssetWeight": 0.30,
            "DefensiveSafeAssetWeight": 0.60,
            "PensionCompliant": True,
            "CAGR": full["CAGR"],
            "MDD": full["MDD"],
            "Sharpe": full["Sharpe"],
            "Calmar": full["Calmar"],
            "CAGRImprovement": full["CAGR"] - full_baseline["CAGR"],
            "MDDImprovement": full["MDD"] - full_baseline["MDD"],
            "RollingWindows": len(rolling),
            "RollingBothWins": sum(row["ImprovesBoth"] for row in rolling),
            "WorstRollingCAGRGap": min(row["CAGRGap"] for row in rolling),
            "WorstRollingMDDImprovement": min(
                row["MDDImprovement"] for row in rolling
            ),
            "SelectedCandidate": drawdown == -0.155,
        })
    return pd.DataFrame(rows)


def run_asymmetric_defense2_validation():
    fixed = pd.DataFrame(
        _comparison(*window) for window in FIXED_WINDOWS
    )
    rolling = pd.DataFrame(
        _comparison(*window) for window in _rolling_windows()
    )
    sensitivity = _sensitivity_report()
    fixed.to_csv(
        RESULT_DIR / "asymmetric_defense2_tuning_windows.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "asymmetric_defense2_tuning_rolling.csv", index=False
    )
    sensitivity.to_csv(
        RESULT_DIR / "asymmetric_defense2_tuning_sensitivity.csv", index=False
    )
    return {"fixed": fixed, "rolling": rolling, "sensitivity": sensitivity}


if __name__ == "__main__":
    reports = run_asymmetric_defense2_validation()
    print(reports["fixed"].to_string(index=False))
    print("\nRolling three-year scorecard")
    print(
        reports["rolling"][[
            "Window", "CAGRImprovement", "MDDImprovement", "ImprovesBoth"
        ]].to_string(index=False)
    )
    print("\nDefensive-drawdown sensitivity")
    print(reports["sensitivity"].to_string(index=False))
