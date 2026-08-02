"""Event attribution for replacing selected BIL with capped VXUS."""

import numpy as np
import pandas as pd

from backtest import Backtest
from config import DATA_DIR, END_DATE, RESULT_DIR, START_DATE
from strategy import PensionVXUSSubstitutionStrategy
from .vxus_bil_walkforward import VXUSBILShareStrategy


HORIZONS = (5, 20, 60)


def forward_path_metrics(series, date, horizon):
    """Return forward return and drawdown over complete trading-day windows."""
    clean = series.dropna().astype(float)
    if date not in clean.index:
        return np.nan, np.nan
    location = clean.index.get_loc(date)
    if not isinstance(location, (int, np.integer)):
        return np.nan, np.nan
    window = clean.iloc[location:location + horizon + 1]
    if len(window) != horizon + 1:
        return np.nan, np.nan
    values = window.to_numpy()
    forward_return = values[-1] / values[0] - 1.0
    running_high = np.maximum.accumulate(values)
    max_drawdown = np.min(values / running_high - 1.0)
    return float(forward_return), float(max_drawdown)


def _run(strategy):
    backtest = Backtest(
        strategy,
        data_dir=DATA_DIR,
        tickers=strategy.required_tickers,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "market_data": backtest.data,
    }


def _events_by_signal_date(rebalances):
    return {
        pd.Timestamp(event["Date"]): event
        for event in rebalances
        if event.get("ExecutionDate") is not None
    }


def _differing_events(baseline, candidate):
    baseline_events = _events_by_signal_date(baseline["rebalances"])
    candidate_events = _events_by_signal_date(candidate["rebalances"])
    events = []
    for signal_date in sorted(set(baseline_events) & set(candidate_events)):
        baseline_event = baseline_events[signal_date]
        candidate_event = candidate_events[signal_date]
        baseline_target = baseline_event["Target"]
        candidate_target = candidate_event["Target"]
        replacement_weight = (
            candidate_target.get("VXUS", 0.0)
            - baseline_target.get("VXUS", 0.0)
        )
        if replacement_weight <= 1e-9:
            continue
        events.append((
            signal_date,
            baseline_event,
            candidate_event,
            replacement_weight,
        ))
    return events


def _event_report(baseline, candidate):
    history_base = baseline["history"]
    history_candidate = candidate["history"]
    market = candidate["market_data"]
    rows = []
    differing_events = _differing_events(baseline, candidate)
    for event_index, (
        signal_date, base_event, candidate_event, replacement_weight
    ) in enumerate(differing_events):
        execution_date = pd.Timestamp(candidate_event["ExecutionDate"])
        next_execution_date = (
            pd.Timestamp(differing_events[event_index + 1][2]["ExecutionDate"])
            if event_index + 1 < len(differing_events)
            else pd.NaT
        )
        target = candidate_event["Target"]
        row = {
            "SignalDate": signal_date,
            "ExecutionDate": execution_date,
            "NextDifferentExecutionDate": next_execution_date,
            "Reason": candidate_event.get("Reason"),
            "State": history_candidate["StrategyState"].get(signal_date),
            "ExecutionDays": candidate_event["ExecutionDays"],
            "BaselineQQQTarget": base_event["Target"].get("QQQ", 0.0),
            "BaselineBILTarget": base_event["Target"].get("BIL", 0.0),
            "CandidateQQQTarget": target.get("QQQ", 0.0),
            "CandidateBILTarget": target.get("BIL", 0.0),
            "CandidateVXUSTarget": target.get("VXUS", 0.0),
            "ReplacementWeight": replacement_weight,
        }
        for horizon in HORIZONS:
            base_return, base_mdd = forward_path_metrics(
                history_base["Portfolio"], execution_date, horizon
            )
            candidate_return, candidate_mdd = forward_path_metrics(
                history_candidate["Portfolio"], execution_date, horizon
            )
            vxus_return, vxus_mdd = forward_path_metrics(
                market["VXUS_Close"], execution_date, horizon
            )
            bil_return, _ = forward_path_metrics(
                market["BIL_Close"], execution_date, horizon
            )
            asset_edge = vxus_return - bil_return
            execution_location = history_candidate.index.get_loc(
                execution_date
            )
            horizon_end_location = execution_location + horizon
            horizon_end_date = (
                history_candidate.index[horizon_end_location]
                if horizon_end_location < len(history_candidate.index)
                else pd.NaT
            )
            overlaps_next_event = (
                pd.notna(next_execution_date)
                and pd.notna(horizon_end_date)
                and next_execution_date <= horizon_end_date
            )
            row.update({
                f"HorizonEndDate{horizon}D": horizon_end_date,
                f"OverlapsNextEvent{horizon}D": overlaps_next_event,
                f"BaselineReturn{horizon}D": base_return,
                f"CandidateReturn{horizon}D": candidate_return,
                f"PortfolioGap{horizon}D": candidate_return - base_return,
                f"BaselineMaxDrawdown{horizon}D": base_mdd,
                f"CandidateMaxDrawdown{horizon}D": candidate_mdd,
                f"DrawdownGap{horizon}D": candidate_mdd - base_mdd,
                f"VXUSReturn{horizon}D": vxus_return,
                f"BILReturn{horizon}D": bil_return,
                f"VXUSMinusBIL{horizon}D": asset_edge,
                f"TargetWeightedAssetEdge{horizon}D": (
                    replacement_weight * asset_edge
                ),
                f"VXUSMaxDrawdown{horizon}D": vxus_mdd,
            })
        rows.append(row)
    return pd.DataFrame(rows)


def _summary(events):
    rows = []
    for horizon in HORIZONS:
        gap = events[f"PortfolioGap{horizon}D"].dropna()
        drawdown_gap = events[f"DrawdownGap{horizon}D"].dropna()
        asset_edge = events[f"VXUSMinusBIL{horizon}D"].dropna()
        weighted_edge = events[
            f"TargetWeightedAssetEdge{horizon}D"
        ].dropna()
        non_overlapping = events.loc[
            ~events[f"OverlapsNextEvent{horizon}D"],
            f"PortfolioGap{horizon}D",
        ].dropna()
        rows.append({
            "HorizonDays": horizon,
            "Events": len(gap),
            "NonOverlappingEvents": len(non_overlapping),
            "PortfolioWinRate": (gap > 0.0).mean(),
            "AveragePortfolioGap": gap.mean(),
            "MedianPortfolioGap": gap.median(),
            "WorstPortfolioGap": gap.min(),
            "AverageNonOverlappingPortfolioGap": non_overlapping.mean(),
            "AverageDrawdownGap": drawdown_gap.mean(),
            "WorstDrawdownGap": drawdown_gap.min(),
            "VXUSBeatBILRate": (asset_edge > 0.0).mean(),
            "AverageVXUSMinusBIL": asset_edge.mean(),
            "SumTargetWeightedAssetEdge": weighted_edge.sum(),
        })
    return pd.DataFrame(rows)


def run_vxus_bil_event_attribution():
    """Compare the BND-only baseline with BND-or-BIL VXUS substitution."""
    baseline = _run(PensionVXUSSubstitutionStrategy())
    candidate = _run(VXUSBILShareStrategy(1.0))
    events = _event_report(baseline, candidate)
    summary = _summary(events)
    reports = {
        "vxus_bil_event_attribution": events,
        "vxus_bil_event_attribution_summary": summary,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_vxus_bil_event_attribution()
    print(output["vxus_bil_event_attribution"].to_string(index=False))
    print("\nEvent summary")
    print(output["vxus_bil_event_attribution_summary"].to_string(index=False))
