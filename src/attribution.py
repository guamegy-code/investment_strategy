"""퇴직연금 자산배분의 시장 상태와 전환 판단을 분석한다."""

import numpy as np
import pandas as pd


class RetirementAllocationAttribution:
    """상태별 수익을 분해하고 상태 전환 판단을 평가한다.

    t일 종가에서 관찰한 상태는 해당 종가부터 다음 종가까지의 수익률과
    연결한다. 다음 거래일 시가 이후에 주문을 실행하는 백테스트 엔진의
    처리 순서와 일치하도록 구성한 것이다.
    """

    HORIZONS = (5, 20, 60)
    PEAK_LOOKBACK = 120

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
    def _forward_path_metrics(prices, horizon):
        """Calculate QQQ outcomes over the next ``horizon`` sessions.

        The state recorded at today's close is evaluated only with prices that
        occur after that close.  Incomplete windows at the end of the sample
        remain missing instead of being treated as shorter observations.
        """
        prices = prices.astype(float)
        rows = []
        for index in range(len(prices)):
            window = prices.iloc[index:index + horizon + 1]
            if len(window) != horizon + 1 or window.isna().any():
                rows.append((np.nan,) * 4)
                continue

            values = window.to_numpy()
            future_return = values[-1] / values[0] - 1.0
            running_high = np.maximum.accumulate(values)
            max_drawdown = np.min(values / running_high - 1.0)
            max_upside = np.max(values / values[0] - 1.0)
            daily_returns = values[1:] / values[:-1] - 1.0
            volatility = (
                np.std(daily_returns, ddof=1) * np.sqrt(252)
                if len(daily_returns) > 1
                else 0.0
            )
            rows.append((future_return, max_drawdown, max_upside, volatility))

        return pd.DataFrame(
            rows,
            index=prices.index,
            columns=[
                "ForwardReturn",
                "ForwardMaxDrawdown",
                "ForwardMaxUpside",
                "ForwardVolatility",
            ],
        )

    def state_market_quality(self):
        """Describe the future QQQ market path observed in each strategy state.

        This report evaluates classification quality independently from the
        portfolio weights.  It is descriptive rather than a trading return:
        daily observations overlap, especially at the 20- and 60-day horizons.
        """
        if "StrategyState" not in self.frame:
            return pd.DataFrame()

        valid_states = self.frame["StrategyState"].dropna()
        if valid_states.empty:
            return pd.DataFrame()

        metrics_by_horizon = {
            horizon: self._forward_path_metrics(
                self.frame["QQQ_Close"], horizon
            )
            for horizon in self.HORIZONS
        }
        state_order = ["BULL", "CAUTION", "BEAR", "RECOVERY"]
        observed_states = list(dict.fromkeys(valid_states.astype(str)))
        ordered_states = [
            state for state in state_order if state in observed_states
        ] + [
            state for state in observed_states if state not in state_order
        ]

        rows = []
        for state in ordered_states:
            state_mask = self.frame["StrategyState"] == state
            row = {
                "State": state,
                "Days": int(state_mask.sum()),
                "ShareOfDays": float(state_mask.mean()),
            }
            for horizon, metrics in metrics_by_horizon.items():
                sample = metrics.loc[state_mask].dropna()
                returns = sample["ForwardReturn"]
                drawdowns = sample["ForwardMaxDrawdown"]
                row.update({
                    f"Samples{horizon}D": len(sample),
                    f"AvgForwardReturn{horizon}D": returns.mean(),
                    f"MedianForwardReturn{horizon}D": returns.median(),
                    f"PositiveRate{horizon}D": (returns > 0).mean(),
                    f"AvgForwardMaxDrawdown{horizon}D": drawdowns.mean(),
                    f"WorstForwardMaxDrawdown{horizon}D": drawdowns.min(),
                    f"AvgForwardMaxUpside{horizon}D": sample[
                        "ForwardMaxUpside"
                    ].mean(),
                    f"AvgForwardVolatility{horizon}D": sample[
                        "ForwardVolatility"
                    ].mean(),
                    f"Drawdown5PctRate{horizon}D": (drawdowns <= -0.05).mean(),
                    f"Drawdown10PctRate{horizon}D": (drawdowns <= -0.10).mean(),
                })
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def _transition_type(previous_weight, new_weight):
        if new_weight < previous_weight:
            return "DEFENSIVE"
        if new_weight > previous_weight:
            return "RISK_INCREASE"
        return "LATERAL"

    @staticmethod
    def _signal_direction(previous_state, new_state):
        risk_off = {
            ("BULL", "CAUTION"),
            ("BULL", "BEAR"),
            ("CAUTION", "BEAR"),
            ("RECOVERY", "CAUTION"),
            ("RECOVERY", "BEAR"),
        }
        risk_on = {
            ("BEAR", "RECOVERY"),
            ("CAUTION", "BULL"),
            ("RECOVERY", "BULL"),
        }
        transition = (previous_state, new_state)
        if transition in risk_off:
            return "RISK_OFF"
        if transition in risk_on:
            return "RISK_ON"
        return "LATERAL"

    def _state_path_after(self, date):
        if "StrategyState" not in self.frame or date not in self.frame.index:
            return pd.Series(dtype=object)
        location = self.frame.index.get_loc(date)
        if not isinstance(location, (int, np.integer)):
            return pd.Series(dtype=object)
        return self.frame["StrategyState"].iloc[location:]

    def _state_transition_path(self, date, current_state):
        path = self._state_path_after(date)
        if path.empty:
            return np.nan, None, pd.NaT
        changed = path[path != current_state]
        if changed.empty:
            return len(path), None, pd.NaT
        next_date = changed.index[0]
        duration = int(self.frame.index.get_loc(next_date) - self.frame.index.get_loc(date))
        return duration, changed.iloc[0], next_date

    def _prior_peak_metrics(self, prices, date):
        if date not in prices.index:
            return np.nan, np.nan, pd.NaT
        location = prices.index.get_loc(date)
        if not isinstance(location, (int, np.integer)):
            return np.nan, np.nan, pd.NaT
        start = max(0, location - self.PEAK_LOOKBACK + 1)
        lookback = prices.iloc[start:location + 1].dropna()
        if lookback.empty:
            return np.nan, np.nan, pd.NaT
        peak_date = lookback.idxmax()
        peak_location = prices.index.get_loc(peak_date)
        days_from_peak = int(location - peak_location)
        drawdown_from_peak = prices.iloc[location] / lookback.loc[peak_date] - 1.0
        return days_from_peak, float(drawdown_from_peak), peak_date

    def transition_events(self):
        qqq = self.frame["QQQ_Close"]
        portfolio = self.frame["Portfolio"]
        benchmark = self.frame.get("BenchmarkPortfolio")
        path_metrics = {
            horizon: self._forward_path_metrics(qqq, horizon)
            for horizon in self.HORIZONS
        }
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
            transition = transition.split("|", 1)[0]
            previous_state, new_state = transition.split("->", 1)
            allocation_states = {"BULL", "CAUTION", "BEAR", "RECOVERY"}
            if (
                previous_state not in allocation_states
                or new_state not in allocation_states
            ):
                continue
            transition_type = self._transition_type(previous_weight, new_weight)
            signal_direction = self._signal_direction(previous_state, new_state)
            state_duration, next_state, next_state_date = (
                self._state_transition_path(date, new_state)
            )
            days_from_peak, drawdown_from_peak, peak_date = (
                self._prior_peak_metrics(qqq, date)
            )
            row = {
                "Date": date,
                "ExecutionDate": rebalance.get("ExecutionDate", pd.NaT),
                "Transition": transition,
                "Type": transition_type,
                "SignalDirection": signal_direction,
                "PreviousState": previous_state,
                "NewState": new_state,
                "PreviousQQQWeight": previous_weight,
                "NewQQQWeight": new_weight,
                "Prior120DPeakDate": peak_date,
                "DaysFromPrior120DHigh": days_from_peak,
                "DrawdownFromPrior120DHigh": drawdown_from_peak,
                "StateDurationDays": state_duration,
                "NextState": next_state,
                "NextStateDate": next_state_date,
                "ShortLivedState5D": state_duration <= 5
                if pd.notna(state_duration)
                else np.nan,
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
                if date in path_metrics[horizon].index:
                    path = path_metrics[horizon].loc[date]
                    row[f"QQQForwardMaxDrawdown{horizon}D"] = path[
                        "ForwardMaxDrawdown"
                    ]
                    row[f"QQQForwardMaxUpside{horizon}D"] = path[
                        "ForwardMaxUpside"
                    ]
                else:
                    row[f"QQQForwardMaxDrawdown{horizon}D"] = np.nan
                    row[f"QQQForwardMaxUpside{horizon}D"] = np.nan

            forward20 = row["QQQForwardReturn20D"]
            if pd.isna(forward20) or transition_type == "LATERAL":
                row["Success20D"] = np.nan
            elif transition_type == "DEFENSIVE":
                row["Success20D"] = forward20 < 0
            else:
                row["Success20D"] = forward20 > 0

            if pd.isna(forward20) or signal_direction == "LATERAL":
                row["DirectionalSuccess20D"] = np.nan
                row["DirectionalFalseAlarm20D"] = np.nan
            elif signal_direction == "RISK_OFF":
                row["DirectionalSuccess20D"] = forward20 < 0
                row["DirectionalFalseAlarm20D"] = forward20 > 0
            else:
                row["DirectionalSuccess20D"] = forward20 > 0
                row["DirectionalFalseAlarm20D"] = forward20 < 0

            drawdown20 = row["QQQForwardMaxDrawdown20D"]
            row["NoMeaningfulDownside20D"] = (
                bool(drawdown20 > -0.05)
                if signal_direction == "RISK_OFF" and pd.notna(drawdown20)
                else np.nan
            )
            row["BearRebound20D"] = (
                forward20 > 0
                if new_state == "BEAR" and pd.notna(forward20)
                else np.nan
            )

            future_states = self._state_path_after(date).iloc[1:]
            for horizon in (20, 60):
                horizon_states = future_states.iloc[:horizon]
                relapsed = (
                    bool((horizon_states == "BEAR").any())
                    if new_state == "RECOVERY" and len(horizon_states) == horizon
                    else np.nan
                )
                row[f"RecoveryRelapseToBear{horizon}D"] = relapsed
            row["DaysUntilBearRelapse"] = (
                state_duration
                if new_state == "RECOVERY" and next_state == "BEAR"
                else np.nan
            )
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

    def transition_quality(self):
        """Summarize timeliness, false alarms, rebounds, and state relapses."""
        events = self.transition_events()
        if events.empty:
            return events

        aggregations = {
            "Count": ("Transition", "size"),
            "DirectionalSuccessRate20D": ("DirectionalSuccess20D", "mean"),
            "DirectionalFalseAlarmRate20D": (
                "DirectionalFalseAlarm20D", "mean"
            ),
            "NoMeaningfulDownsideRate20D": (
                "NoMeaningfulDownside20D", "mean"
            ),
            "BearReboundRate20D": ("BearRebound20D", "mean"),
            "RecoveryRelapseRate20D": (
                "RecoveryRelapseToBear20D", "mean"
            ),
            "RecoveryRelapseRate60D": (
                "RecoveryRelapseToBear60D", "mean"
            ),
            "AvgDaysUntilBearRelapse": ("DaysUntilBearRelapse", "mean"),
            "AvgStateDurationDays": ("StateDurationDays", "mean"),
            "ShortLivedStateRate5D": ("ShortLivedState5D", "mean"),
            "AvgDaysFromPrior120DHigh": ("DaysFromPrior120DHigh", "mean"),
            "AvgDrawdownFromPrior120DHigh": (
                "DrawdownFromPrior120DHigh", "mean"
            ),
            "AvgQQQForwardReturn20D": ("QQQForwardReturn20D", "mean"),
            "AvgQQQForwardMaxDrawdown20D": (
                "QQQForwardMaxDrawdown20D", "mean"
            ),
        }
        return (
            events.groupby(["Transition", "SignalDirection"], dropna=False)
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
            if year_events.empty:
                defense = year_events
                increase = year_events
            else:
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
            "state_market_quality": self.state_market_quality(),
            "transition_events": self.transition_events(),
            "transition_summary": self.transition_summary(),
            "transition_quality": self.transition_quality(),
            "yearly_attribution": self.yearly_summary(),
        }
