import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import Backtest, _ValuationAwarePortfolio
from portfolio import Portfolio
from runner import Runner


class DummyStrategy:
    pass


class InMemoryBacktest(Backtest):
    def load_one(self, ticker):
        dates = pd.bdate_range("2010-01-01", "2013-01-10")
        return pd.DataFrame({"Close": range(len(dates))}, index=dates)


class MarketFieldBacktest(InMemoryBacktest):
    def load_one(self, ticker):
        frame = super().load_one(ticker)
        frame["DISPARITY60"] = 111.0
        return frame


class BacktestDateRangeTests(unittest.TestCase):
    def test_local_signal_portfolio_weights_use_valuation_prices(self):
        portfolio = Portfolio()
        portfolio.cash = 0.0
        portfolio.positions = {"QQQ": 0.0005, "KOSPI": 0.005}
        view = _ValuationAwarePortfolio(
            portfolio, {"QQQ": 1000.0, "KOSPI": 100.0}
        )

        weights = view.weights({"QQQ": 100.0, "KOSPI": 100.0})

        self.assertAlmostEqual(weights["QQQ"], 0.5)
        self.assertAlmostEqual(weights["KOSPI"], 0.5)

    def test_market_snapshot_includes_disparity60(self):
        backtest = MarketFieldBacktest(
            DummyStrategy(), tickers=("QQQ",), start_date="2012-01-01"
        )
        row = backtest.data.iloc[0].to_dict()

        self.assertEqual(backtest.get_market(row)["QQQ"]["DISPARITY60"], 111.0)

    def test_configured_dates_filter_the_merged_market_data(self):
        backtest = InMemoryBacktest(
            DummyStrategy(),
            tickers=("QQQ", "BND"),
            start_date="2012-01-01",
            end_date="2012-12-31",
        )

        self.assertEqual(backtest.data.index.min(), pd.Timestamp("2012-01-02"))
        self.assertEqual(backtest.data.index.max(), pd.Timestamp("2012-12-31"))

    def test_omitted_dates_preserve_the_available_data_range(self):
        backtest = InMemoryBacktest(DummyStrategy(), tickers=("QQQ",))

        self.assertEqual(backtest.data.index.min(), pd.Timestamp("2010-01-01"))
        self.assertEqual(backtest.data.index.max(), pd.Timestamp("2013-01-10"))

    def test_runner_reports_common_period_outside_summary_columns(self):
        runner = Runner()
        summary = {
            "Strategy": "TEST",
            "StartDate": pd.Timestamp("2012-01-03"),
            "EndDate": pd.Timestamp("2012-12-31"),
            "CAGR": 0.1,
            "MDD": -0.1,
            "Volatility": 0.1,
            "Sharpe": 0.5,
            "Sortino": 0.7,
            "Calmar": 1.0,
            "TransactionCosts": 0.0,
            "Start": 1.0,
            "End": 1.1,
        }
        runner.results = [{"summary": summary}]

        result_summary = runner.summary()

        self.assertEqual(
            runner.backtest_period(), "2012-01-03 ~ 2012-12-31"
        )
        self.assertNotIn("BacktestPeriod", result_summary.columns)

    def test_runner_reports_when_strategy_periods_vary(self):
        first = {
            "StartDate": pd.Timestamp("2012-01-03"),
            "EndDate": pd.Timestamp("2012-12-31"),
        }
        second = {
            "StartDate": pd.Timestamp("2013-01-02"),
            "EndDate": pd.Timestamp("2013-12-31"),
        }
        runner = Runner()
        runner.results = [{"summary": first}, {"summary": second}]

        self.assertEqual(runner.backtest_period(), "varies by strategy")


if __name__ == "__main__":
    unittest.main()
