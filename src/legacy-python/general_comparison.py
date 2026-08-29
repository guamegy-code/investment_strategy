"""Canonical reports for ordinary strategy-to-strategy comparisons.

Research that depends on later external data, walk-forward folds, or extended
stress history keeps its own period.  Such results are explicitly excluded
from this report instead of silently shortening every ordinary comparison.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from config import GENERAL_COMPARISON_START_DATE
from performance import Performance
from strategy_domain import strategy_display_name


GENERAL_COMPARISON_SCOPE = "GENERAL_2012"
DATA_AVAILABILITY_EXCEPTION = "DATA_AVAILABILITY_EXCEPTION"


def _strategy_name(result: dict[str, Any]) -> str:
    summary_name = result.get("summary", {}).get("Strategy")
    if summary_name:
        return str(summary_name)
    return strategy_display_name(result["strategy"])


def _history_bounds(history: pd.DataFrame):
    if history.empty:
        return pd.NaT, pd.NaT
    return pd.Timestamp(history.index.min()), pd.Timestamp(history.index.max())


def general_comparison_reports(
    results: Iterable[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return canonical comparison rows and explicitly excluded strategies."""
    results = list(results)
    canonical_start = pd.Timestamp(GENERAL_COMPARISON_START_DATE)
    eligible = []
    exception_rows = []

    for result in results:
        history = result["history"]
        actual_start, actual_end = _history_bounds(history)
        name = _strategy_name(result)
        if pd.isna(actual_start) or actual_start > canonical_start:
            exception_rows.append({
                "Strategy": name,
                "ComparisonScope": DATA_AVAILABILITY_EXCEPTION,
                "CanonicalStartDate": canonical_start,
                "ActualStartDate": actual_start,
                "ActualEndDate": actual_end,
                "Reason": (
                    "EMPTY_HISTORY"
                    if pd.isna(actual_start)
                    else "REQUIRED_DATA_STARTS_AFTER_CANONICAL_DATE"
                ),
            })
            continue
        eligible.append((result, actual_end))

    columns = [
        "Strategy",
        "ComparisonScope",
        "StartDate",
        "EndDate",
        "Observations",
        "CAGR",
        "MDD",
        "Volatility",
        "Sharpe",
        "Sortino",
        "Calmar",
        "TransactionCosts",
    ]
    if not eligible:
        return pd.DataFrame(columns=columns), pd.DataFrame(exception_rows)

    common_end = min(actual_end for _, actual_end in eligible)
    rows = []
    for result, _ in eligible:
        sample = result["history"].loc[canonical_start:common_end]
        if sample.empty:
            continue
        metrics = Performance(sample).summary()
        costs = sample.get("TransactionCosts")
        transaction_costs = 0.0
        if costs is not None and not costs.empty:
            transaction_costs = float(costs.iloc[-1] - costs.iloc[0])
        rows.append({
            "Strategy": _strategy_name(result),
            "ComparisonScope": GENERAL_COMPARISON_SCOPE,
            "StartDate": pd.Timestamp(sample.index.min()),
            "EndDate": pd.Timestamp(sample.index.max()),
            "Observations": len(sample),
            "CAGR": metrics["CAGR"],
            "MDD": metrics["MDD"],
            "Volatility": metrics["Volatility"],
            "Sharpe": metrics["Sharpe"],
            "Sortino": metrics["Sortino"],
            "Calmar": metrics["Calmar"],
            "TransactionCosts": transaction_costs,
        })
    return pd.DataFrame(rows, columns=columns), pd.DataFrame(exception_rows)


def write_general_comparison_reports(
    results: Iterable[dict[str, Any]],
    result_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Persist canonical and exception reports for the general strategy suite."""
    comparison, exceptions = general_comparison_reports(results)
    comparison.to_csv(
        result_dir / "general_strategy_comparison.csv",
        index=False,
    )
    exceptions.to_csv(
        result_dir / "general_strategy_comparison_exceptions.csv",
        index=False,
    )
    return comparison, exceptions
