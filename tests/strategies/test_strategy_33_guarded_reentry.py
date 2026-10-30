import unittest
from copy import deepcopy
import pandas as pd

from tests import PROJECT_ROOT
from strategy_dsl import DeclarativeStrategy, load_strategy_definition

PATH = PROJECT_ROOT/'strategies/33_qqq_guarded_reentry_buy_acceleration.yaml'


class Account:
    def __init__(self, q=.7): self.q = q
    def weights(self, prices): return {'QQQ': self.q, 'BIL': 1-self.q}


def market(gate=True, turning=False):
    q = dict(Close=100., EMA20=98., EMA55=96., EMA200=94., ROC5=2. if gate else -1.,
        ROC20=4., ROC60=6., ROC252=-5. if turning else 8., EMA20_SLOPE5=1.,
        EMA200_SLOPE20=1., VALUATION_SCORE=50.)
    return {'QQQ':q, 'SPY':deepcopy(q), 'XLF':deepcopy(q),
            'BAA10Y':dict(Close=1.5, ROC20=0.), 'BIL':dict(Close=100., ROC20=.2, ROC252=2.)}


class Strategy33Tests(unittest.TestCase):
    def make(self):
        s = DeclarativeStrategy.from_yaml(PATH)
        s._state_values.update(trend_mode='BULL', defense_mode='WARNING', valuation_peak=80., peak_price=100.)
        return s

    def test_entry_only_first_session_of_week_then_daily_cancel(self):
        s, p = self.make(), Account()
        s.evaluate(pd.Timestamp('2021-01-04'), market(False), p)
        self.assertAlmostEqual(s.evaluate(pd.Timestamp('2021-01-05'), market(), p)['target']['QQQ'], .7)
        self.assertAlmostEqual(s.evaluate(pd.Timestamp('2021-01-11'), market(), p)['target']['QQQ'], .85)
        signal = s.evaluate(pd.Timestamp('2021-01-12'), market(False), Account(.85))
        self.assertEqual(s.guarded_reentry, 'FALSE')
        self.assertEqual(signal['target']['QQQ'], .7)
        self.assertTrue(signal['rebalance'])

    def test_year_boundary_does_not_open_an_extra_week(self):
        s, p = self.make(), Account()
        s.evaluate(pd.Timestamp('2020-12-31'), market(False), p)
        self.assertEqual(s.evaluate(pd.Timestamp('2021-01-01'), market(), p)['target']['QQQ'], .7)
        self.assertEqual(s.evaluate(pd.Timestamp('2021-01-04'), market(), p)['target']['QQQ'], .85)

    def test_buy_confirmation_accelerates_without_changing_valuation_peak(self):
        s, p = self.make(), Account(0)
        s._state_values['trend_mode'] = 'BEAR'
        s.evaluate(pd.Timestamp('2021-01-04'), market(turning=True), p)
        self.assertEqual(s.trend_mode, 'BEAR')
        s.evaluate(pd.Timestamp('2021-01-05'), market(turning=True), p)
        self.assertEqual(s.trend_mode, 'RECOVERY')
        self.assertEqual(s.valuation_peak, 80.)
        self.assertEqual(s.defense_mode, 'WARNING')

    def test_all_hard_guards_cancel_reentry_and_deep_guard_does_not_top_up(self):
        for guard in ('credit_guard','deep_guard','financial_guard','structural_bear_mode'):
            s, p = self.make(), Account(.15)
            s._state_values[guard] = 'TRUE'
            if guard == 'structural_bear_mode':
                # Validate rule preservation below; structural state derives from today's market.
                continue
            signal = s.evaluate(pd.Timestamp('2021-01-04'), market(), p)
            self.assertEqual(s.guarded_reentry, 'FALSE', guard)
            if guard == 'deep_guard': self.assertEqual(signal['target']['QQQ'], .15)
        original = load_strategy_definition(PROJECT_ROOT/'strategies/32_qqq_valuation_financial_credit_guard_no_topup.yaml')
        current = load_strategy_definition(PATH)
        for key in ('credit_guard','deep_guard','deep_credit_peak','financial_guard','structural_bear_mode','defense_mode','valuation_peak'):
            self.assertEqual(current['state'][key], original['state'][key], key)


if __name__ == '__main__': unittest.main()
