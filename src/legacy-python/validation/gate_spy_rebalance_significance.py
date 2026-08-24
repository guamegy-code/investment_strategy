"""Measure whether Gate SPY rebalances change exposure meaningfully."""

from pathlib import Path

import pandas as pd

from backtest import Backtest
from strategy_dsl import DeclarativeStrategy


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "13_profit_band_tdf2050_gate_spy_tdf100.yaml"
RESULT = ROOT / "results" / "gate_spy_rebalance_significance.csv"


def max_weight_change(event):
    target = event["Target"]
    previous = event.get("PreWeights", {})
    return max(abs(target.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in target)


def target_turnover(event):
    target = event["Target"]
    previous = event.get("PreWeights", {})
    return sum(abs(target.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in target) / 2


def event_type(event):
    reason = event.get("Reason") or "INITIAL"
    if reason == "DECLARATIVE_RULE_4":
        return "TDF_EXIT" if event["Target"].get("TDF2050_PROXY", 0.0) < 0.01 else "TDF_RESTORE"
    if "->" in reason:
        return "STATE_TRANSITION"
    if reason == "DECLARATIVE_RULE_5":
        return "BND_BIL_ROTATION"
    if reason == "DECLARATIVE_RULE_1":
        return "QQQ_CAP"
    return reason


def main():
    strategy = DeclarativeStrategy.from_yaml(SOURCE)
    backtest = Backtest(strategy, tickers=strategy.required_tickers)
    _, _, rebalances = backtest.run_all()
    qqq = backtest.data["QQQ_Close"]
    tdf = backtest.data["TDF2050_PROXY_Close"]
    bil = backtest.data["BIL_Close"]
    rows = []
    for event in rebalances:
        date = event["Date"]
        position = qqq.index.get_indexer([date], method="nearest")[0]
        future = min(position + 21, len(qqq) - 1)
        tdf_return = tdf.iloc[future] / tdf.iloc[position] - 1
        bil_return = bil.iloc[future] / bil.iloc[position] - 1
        kind = event_type(event)
        switch_benefit = (
            bil_return - tdf_return
            if kind == "TDF_EXIT"
            else tdf_return - bil_return
            if kind == "TDF_RESTORE"
            else float("nan")
        )
        rows.append({
            "Date": date,
            "Reason": event.get("Reason") or "INITIAL",
            "Type": kind,
            "MaxWeightChange": max_weight_change(event),
            "TargetTurnover": target_turnover(event),
            "QQQReturn21D": qqq.iloc[future] / qqq.iloc[position] - 1,
            "TDFReturn21D": tdf_return,
            "BILReturn21D": bil_return,
            "SwitchBenefit21D": switch_benefit,
            "TDFWeight": event["Target"].get("TDF2050_PROXY", 0.0),
        })
    events = pd.DataFrame(rows).sort_values("Date")
    events["ReversedWithin21D"] = False
    switches = events.loc[events["Type"].isin(["TDF_EXIT", "TDF_RESTORE"])]
    for index, event in switches.iterrows():
        opposite = "TDF_RESTORE" if event["Type"] == "TDF_EXIT" else "TDF_EXIT"
        reversal = (
            (switches["Date"] > event["Date"])
            & (switches["Date"] <= event["Date"] + pd.Timedelta(days=31))
            & (switches["Type"] == opposite)
        )
        events.loc[index, "ReversedWithin21D"] = bool(reversal.any())
    events.to_csv(RESULT, index=False)
    summary = events.groupby("Type").agg(
        Events=("Type", "size"),
        MeanMaxWeightChange=("MaxWeightChange", "mean"),
        MeanTurnover=("TargetTurnover", "mean"),
        MeanQQQReturn21D=("QQQReturn21D", "mean"),
        PositiveQQQ21D=("QQQReturn21D", lambda values: (values > 0).mean()),
        MeanSwitchBenefit21D=("SwitchBenefit21D", "mean"),
        PositiveSwitchBenefit21D=(
            "SwitchBenefit21D", lambda values: (values > 0).mean()
        ),
        ReversedWithin21D=("ReversedWithin21D", "mean"),
    )
    print(summary.to_string(float_format=lambda value: f"{value:.2%}"))


if __name__ == "__main__":
    main()
