"""Account feedback, clocks, frozen delayed orders and allocation constraints."""
from tests import PROJECT_ROOT
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT / 'src/legacy-python/validation'))
from pension_risk_allocation_review import cppi_cap, execute_order, product_mapper, risk_features, simulate
from portfolio import Portfolio


class ScheduledSource:
    required_tickers = ('QQQ', 'BIL')
    risk_asset_tickers = ('QQQ',)

    def evaluate(self, date, market, portfolio):
        risk = market['QQQ']['Close']
        return {'target': {'QQQ': risk, 'BIL': 1-risk}, 'rebalance': True, 'days': 1}


class FeedbackSource(ScheduledSource):
    def evaluate(self, date, market, portfolio):
        risk = min(.2, portfolio.weights({'QQQ': 1, 'BIL': 1})['QQQ'])
        return {'target': {'QQQ': risk, 'BIL': 1-risk}, 'rebalance': True, 'days': 1}


class PensionRiskAllocationTests(unittest.TestCase):
    def fixture(self):
        dates = pd.bdate_range('2025-01-01', periods=8)
        prices = {s: pd.DataFrame({'Open': 100., 'Close': 100.}, index=dates)
                  for s in ('R1', 'R2', 'CASH')}
        bundle = {'Source': ScheduledSource(), 'Date': dates[0], 'InitialTarget': {'QQQ': 1., 'BIL': 0.},
                  'Markets': {dates[1]: {'QQQ': {'Close': .5}}, dates[2]: {'QQQ': {'Close': 0.}}}}
        return dates, prices, bundle

    def test_future_source_close_cannot_change_local_open(self):
        dates, prices, bundle = self.fixture()
        history, _, activity, _ = simulate(prices, bundle, 'BASE', str(dates[1].date()), str(dates[-1].date()), cost=0)
        self.assertEqual(list(history.RiskWeight.iloc[1:4]), [1., .5, 0.])
        self.assertTrue((activity.USSourceDate < activity.Date).all())
        self.assertTrue((activity.PolicyAsOf < activity.Date).all())

    def test_delayed_targets_remain_frozen_and_prefix_invariant(self):
        dates, prices, bundle = self.fixture()
        history, _, activity, _ = simulate(prices, bundle, 'BASE', str(dates[1].date()), str(dates[-1].date()), cost=0, delay=1)
        self.assertEqual(list(history.RiskWeight.iloc[1:5]), [0., 1., .5, 0.])
        executed = activity[activity.Executed]
        self.assertTrue((executed.OrderDecisionDate < executed.Date).all())
        cutoff = dates[4]
        prefix = simulate({s: f.loc[:cutoff] for s, f in prices.items()}, bundle, 'BASE',
                          str(dates[1].date()), str(cutoff.date()), cost=0, delay=1)[0]
        pd.testing.assert_frame_equal(prefix, history.loc[:cutoff])

    def test_mapper_feedback_prevents_rebuy_in_deep_defense(self):
        portfolio = Portfolio()
        portfolio.cash = 0.
        portfolio.positions = {'R1': .04, 'R2': .06, 'CASH': .9}
        market = {s: {'Close': 1.} for s in ('R1', 'R2', 'CASH', 'QQQ', 'BIL')}
        result = product_mapper(FeedbackSource()).evaluate(pd.Timestamp('2025-01-02'), market, portfolio)
        self.assertAlmostEqual(result['target']['R1']+result['target']['R2'], .1)

    def test_monthly_mix_preserves_cash_shares_after_open_gap_and_costs(self):
        portfolio = Portfolio(commission=.00045, slippage=.00060)
        portfolio.cash = 100_000_000.
        portfolio.rebalance({'R1': 100., 'R2': 100., 'CASH': 100.}, {'R1': .4, 'R2': .4, 'CASH': .2})
        before = portfolio.positions['CASH']
        execute_order(portfolio, {'R1': 130., 'R2': 70., 'CASH': 101.},
                      {'Target': {'R1': .5, 'R2': .5, 'CASH': 0.}, 'MixOnly': True, 'Share': .5, 'Reason': 'MIX'}, 'test')
        self.assertAlmostEqual(portfolio.positions['CASH'], before)
        self.assertGreaterEqual(portfolio.cash, -1e-6)

    def test_risk_features_use_only_common_completed_returns(self):
        dates = pd.bdate_range('2020-01-01', periods=100)
        prices = {'R1': pd.DataFrame({'Close': np.exp(np.sin(np.arange(100))/100)}, index=dates),
                  'R2': pd.DataFrame({'Close': np.exp(np.sin(np.arange(100))/50)}, index=dates)}
        feature = risk_features(prices)
        self.assertTrue(feature.iloc[:63].isna().all())
        self.assertAlmostEqual(feature.iloc[-1], 1/3, delta=.002)
        pd.testing.assert_series_equal(feature.iloc[:80], risk_features({s: f.iloc[:80] for s, f in prices.items()}))

    def test_cppi_prior_close_floor_survives_recovery_without_reset(self):
        self.assertAlmostEqual(cppi_cap(100, 100), 1.)
        self.assertAlmostEqual(cppi_cap(90, 100), 5/9)
        self.assertEqual(cppi_cap(79, 100), 0.)
        self.assertLess(cppi_cap(90, 110), cppi_cap(90, 100))
        dates, prices, bundle = self.fixture()
        for s in ('R1', 'R2'):
            prices[s].loc[dates[2]:, ['Open', 'Close']] = 70.
        history = simulate(prices, bundle, 'CPPI_ONLY', str(dates[1].date()), str(dates[-1].date()), cost=0)[0]
        self.assertAlmostEqual(history.loc[dates[2], 'Cap'], 1.)
        self.assertEqual(history.loc[dates[3], 'Cap'], 0.)
        self.assertLess(history.Portfolio.min()-1, -.2)  # A gap can breach the target floor.


if __name__ == '__main__':
    unittest.main()
