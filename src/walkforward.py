"""Walk-forward comparison of dynamic allocation profiles."""

import numpy as np
import pandas as pd

from config import RISK_FREE_RATE, TRADING_DAYS


class WalkForwardProfileComparison:
    """Select a profile on trailing data and evaluate it in the next year."""

    def __init__(self, histories, benchmark_history, lookback_years=5):
        if len(histories) < 2:
            raise ValueError("At least two candidate profiles are required")
        self.histories = {name: value.copy() for name, value in histories.items()}
        self.benchmark = benchmark_history.copy()
        self.lookback_years = lookback_years

    @staticmethod
    def _returns(history):
        return history["Portfolio"].pct_change().dropna()

    @staticmethod
    def _cagr(returns):
        if returns.empty:
            return np.nan
        growth = (1 + returns).prod()
        return growth ** (TRADING_DAYS / len(returns)) - 1

    @staticmethod
    def _mdd(returns):
        if returns.empty:
            return np.nan
        equity = (1 + returns).cumprod()
        return (equity / equity.cummax() - 1).min()

    @staticmethod
    def _sharpe(returns):
        if returns.empty:
            return np.nan
        volatility = returns.std() * np.sqrt(TRADING_DAYS)
        if volatility == 0 or np.isnan(volatility):
            return np.nan
        excess = returns.mean() * TRADING_DAYS - RISK_FREE_RATE
        return excess / volatility

    @staticmethod
    def _year_slice(returns, start_year, end_year):
        return returns[
            (returns.index.year >= start_year)
            & (returns.index.year <= end_year)
        ]

    def yearly_comparison(self):
        candidate_returns = {
            name: self._returns(history)
            for name, history in self.histories.items()
        }
        benchmark_returns = self._returns(self.benchmark)
        common_start = max(series.index.min() for series in candidate_returns.values())
        common_end = min(series.index.max() for series in candidate_returns.values())
        first_test_year = common_start.year + self.lookback_years
        rows = []

        for test_year in range(first_test_year, common_end.year + 1):
            train_start = test_year - self.lookback_years
            train_end = test_year - 1
            for name, returns in candidate_returns.items():
                train = self._year_slice(returns, train_start, train_end)
                test = self._year_slice(returns, test_year, test_year)
                benchmark_test = self._year_slice(
                    benchmark_returns, test_year, test_year
                )
                if train.empty or test.empty:
                    continue
                test_return = (1 + test).prod() - 1
                benchmark_return = (
                    (1 + benchmark_test).prod() - 1
                    if not benchmark_test.empty
                    else np.nan
                )
                rows.append({
                    "TestYear": test_year,
                    "Profile": name,
                    "TrainStartYear": train_start,
                    "TrainEndYear": train_end,
                    "TrainCAGR": self._cagr(train),
                    "TestReturn": test_return,
                    "TestMDD": self._mdd(test),
                    "BenchmarkReturn": benchmark_return,
                    "BenchmarkGap": test_return - benchmark_return,
                })
        return pd.DataFrame(rows)

    def selected_years(self):
        comparison = self.yearly_comparison()
        if comparison.empty:
            return comparison
        selected_index = comparison.groupby("TestYear")["TrainCAGR"].idxmax()
        selected = comparison.loc[selected_index].sort_values("TestYear").copy()
        selected.rename(
            columns={
                "Profile": "SelectedProfile",
                "TestReturn": "SelectedReturn",
                "TestMDD": "SelectedMDD",
            },
            inplace=True,
        )
        return selected.reset_index(drop=True)

    def aggregate_summary(self):
        comparison = self.yearly_comparison()
        selected = self.selected_years()
        if comparison.empty or selected.empty:
            return pd.DataFrame()

        rows = []
        for name, group in comparison.groupby("Profile"):
            returns = group.sort_values("TestYear")["TestReturn"]
            benchmark = group.sort_values("TestYear")["BenchmarkReturn"]
            rows.append({
                "Profile": name,
                "Years": len(group),
                "GeometricAnnualReturn": (1 + returns).prod() ** (1 / len(group)) - 1,
                "WorstYear": returns.min(),
                "BenchmarkWinRate": (returns > benchmark).mean(),
            })

        selected_returns = selected["SelectedReturn"]
        selected_benchmark = selected["BenchmarkReturn"]
        rows.append({
            "Profile": "WALK_FORWARD_SELECTED",
            "Years": len(selected),
            "GeometricAnnualReturn": (
                (1 + selected_returns).prod() ** (1 / len(selected)) - 1
            ),
            "WorstYear": selected_returns.min(),
            "BenchmarkWinRate": (selected_returns > selected_benchmark).mean(),
        })
        return pd.DataFrame(rows)

    def all_reports(self):
        return {
            "walkforward_yearly_comparison": self.yearly_comparison(),
            "walkforward_selected_years": self.selected_years(),
            "walkforward_summary": self.aggregate_summary(),
        }
