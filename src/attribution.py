"""Diagnostics for dynamic-allocation states and transition decisions."""

import numpy as np
import pandas as pd


class DynamicAllocationAttribution:
    """Attribute returns and evaluate state-transition decisions.

    States observed at day t's close are paired with returns from that close to
    the next close. This matches the engine, which executes the signal no
    earlier than the following session's open.
    """

    HORIZONS = (5, 20, 60)

    def __init__(self, history, market_data, rebalances, benchmark_history=None):
        self.history = history.copy()
        self.market_data = market_data.copy()
        self.rebalances = list(rebalances)
        self.benchmark_history = (
            benchmark_history.copy() if benchmark_history is not None else None
        )
        self.frame = self._build_frame()

    @staticmethod
    def _period_return(series):
        clean = series.dropna()
        if clean.empty:
            return np.nan
        return np.expm1(np.log1p(clean).sum())

    @staticmethod
    def _safe_return(series, date, horizon):
        forward = series.shift(-horizon) / series - 1
        value = forward.get(date, np.nan)
        return float(value) if pd.notna(value) else np.nan

    def _build_frame(self):
        frame = self.history.join(
            self.market_data[["QQQ_Close"]],
            how="left",
        )
        frame["ForwardPortfolioReturn1D"] = (
            frame["Portfolio"].shift(-1) / frame["Portfolio"] - 1
        )
        frame["ForwardQQQReturn1D"] = (
            frame["QQQ_Close"].shift(-1) / frame["QQQ_Close"] - 1
        )

        if "Weights" in frame:
            frame["QQQWeight"] = frame["Weights"].apply(
                lambda value: value.get("QQQ", np.nan)
                if isinstance(value, dict)
                else np.nan
            )

        if self.benchmark_history is not None:
            benchmark = self.benchmark_history["Portfolio"].rename(
                "BenchmarkPortfolio"
            )
            frame = frame.join(benchmark, how="left")
            frame["ForwardBenchmarkReturn1D"] = (
                frame["BenchmarkPortfolio"].shift(-1)
                / frame["BenchmarkPortfolio"]
                - 1
            )
        return frame

    def state_summary(self):
        if "StrategyState" not in self.frame:
            return pd.DataFrame()

        valid = self.frame.dropna(
            subset=["StrategyState", "ForwardPortfolioReturn1D"]
        )
        total_days = len(valid)
        rows = []
        for state, group in valid.groupby("StrategyState", sort=False):
            portfolio_return = self._period_return(
                group["ForwardPortfolioReturn1D"]
            )
            qqq_return = self._period_return(group["ForwardQQQReturn1D"])
            benchmark_return = np.nan
            relative_log_contribution = np.nan

            if "ForwardBenchmarkReturn1D" in group:
                benchmark_return = self._period_return(
                    group["ForwardBenchmarkReturn1D"]
                )
                relative_log_contribution = (
                    np.log1p(group["ForwardPortfolioReturn1D"]).sum()
                    - np.log1p(group["ForwardBenchmarkReturn1D"]).sum()
                )

            rows.append({
                "State": state,
                "Days": len(group),
                "ShareOfDays": len(group) / total_days if total_days else np.nan,
                "AvgQQQWeight": group.get(
                    "QQQWeight", pd.Series(dtype=float)
                ).mean(),
                "PortfolioCompoundedReturn": portfolio_return,
                "QQQCompoundedReturn": qqq_return,
                "BenchmarkCompoundedReturn": benchmark_return,
                "BenchmarkRelativeLogContribution": relative_log_contribution,
                "QQQDownDayRate": (group["ForwardQQQReturn1D"] < 0).mean(),
                "AvgRiskOffScore": group.get(
                    "RiskOffScore", pd.Series(dtype=float)
                ).mean(),
                "AvgRecoveryScore": group.get(
                    "RecoveryScore", pd.Series(dtype=float)
                ).mean(),
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _transition_type(previous_weight, new_weight):
        if new_weight < previous_weight:
            return "DEFENSIVE"
        if new_weight > previous_weight:
            return "RISK_INCREASE"
        return "LATERAL"

    def transition_events(self):
        qqq = self.frame["QQQ_Close"]
        portfolio = self.frame["Portfolio"]
        benchmark = self.frame.get("BenchmarkPortfolio")
        previous_target = None
        rows = []

        for rebalance in sorted(self.rebalances, key=lambda item: item["Date"]):
            date = pd.Timestamp(rebalance["Date"])
            target = rebalance.get("Target", {})
            reason = rebalance.get("Reason") or ""
            new_weight = target.get("QQQ")

            if previous_target is None:
                previous_target = target
                continue

            previous_weight = previous_target.get("QQQ")
            previous_target = target
            if "->" not in reason or previous_weight is None or new_weight is None:
                continue

            transition = reason.split("(", 1)[0]
            transition_type = self._transition_type(previous_weight, new_weight)
            row = {
                "Date": date,
                "Transition": transition,
                "Type": transition_type,
                "PreviousQQQWeight": previous_weight,
                "NewQQQWeight": new_weight,
                "RiskOffScore": self.frame.at[date, "RiskOffScore"]
                if date in self.frame.index
                else np.nan,
                "RecoveryScore": self.frame.at[date, "RecoveryScore"]
                if date in self.frame.index
                else np.nan,
            }

            for horizon in self.HORIZONS:
                qqq_return = self._safe_return(qqq, date, horizon)
                dynamic_return = self._safe_return(portfolio, date, horizon)
                benchmark_return = (
                    self._safe_return(benchmark, date, horizon)
                    if benchmark is not None
                    else np.nan
                )
                row[f"QQQForwardReturn{horizon}D"] = qqq_return
                row[f"PortfolioForwardReturn{horizon}D"] = dynamic_return
                row[f"BenchmarkForwardReturn{horizon}D"] = benchmark_return
                row[f"BenchmarkGap{horizon}D"] = (
                    dynamic_return - benchmark_return
                    if pd.notna(dynamic_return) and pd.notna(benchmark_return)
                    else np.nan
                )

            forward20 = row["QQQForwardReturn20D"]
            if pd.isna(forward20) or transition_type == "LATERAL":
                row["Success20D"] = np.nan
            elif transition_type == "DEFENSIVE":
                row["Success20D"] = forward20 < 0
            else:
                row["Success20D"] = forward20 > 0
            rows.append(row)

        return pd.DataFrame(rows)

    def transition_summary(self):
        events = self.transition_events()
        if events.empty:
            return events

        aggregations = {
            "Count": ("Transition", "size"),
            "SuccessRate20D": ("Success20D", "mean"),
        }
        for horizon in self.HORIZONS:
            aggregations[f"AvgQQQReturn{horizon}D"] = (
                f"QQQForwardReturn{horizon}D", "mean"
            )
            aggregations[f"AvgBenchmarkGap{horizon}D"] = (
                f"BenchmarkGap{horizon}D", "mean"
            )
        return (
            events.groupby(["Transition", "Type"], dropna=False)
            .agg(**aggregations)
            .reset_index()
        )

    def yearly_summary(self):
        events = self.transition_events()
        rows = []
        for year, group in self.frame.groupby(self.frame.index.year):
            dynamic_return = group["Portfolio"].iloc[-1] / group["Portfolio"].iloc[0] - 1
            benchmark_return = np.nan
            if "BenchmarkPortfolio" in group:
                benchmark_return = (
                    group["BenchmarkPortfolio"].iloc[-1]
                    / group["BenchmarkPortfolio"].iloc[0]
                    - 1
                )
            year_events = (
                events[events["Date"].dt.year == year]
                if not events.empty
                else events
            )
            defense = year_events[year_events["Type"] == "DEFENSIVE"]
            increase = year_events[year_events["Type"] == "RISK_INCREASE"]
            costs = group.get("TransactionCosts", pd.Series([0.0], index=[group.index[0]]))
            cost_change = costs.iloc[-1] - costs.iloc[0]

            rows.append({
                "Year": year,
                "PortfolioReturn": dynamic_return,
                "BenchmarkReturn": benchmark_return,
                "BenchmarkGap": dynamic_return - benchmark_return
                if pd.notna(benchmark_return)
                else np.nan,
                "AvgQQQWeight": group.get(
                    "QQQWeight", pd.Series(dtype=float)
                ).mean(),
                "Transitions": len(year_events),
                "DefensiveSignals": len(defense),
                "DefensiveSuccessRate20D": defense["Success20D"].mean()
                if not defense.empty
                else np.nan,
                "RiskIncreaseSignals": len(increase),
                "RiskIncreaseSuccessRate20D": increase["Success20D"].mean()
                if not increase.empty
                else np.nan,
                "TransactionCosts": cost_change,
            })
        return pd.DataFrame(rows)

    def all_reports(self):
        return {
            "state_attribution": self.state_summary(),
            "transition_events": self.transition_events(),
            "transition_summary": self.transition_summary(),
            "yearly_attribution": self.yearly_summary(),
        }
