"""Self-financing attribution and causal, fully matured action labels."""
from tests import PROJECT_ROOT
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
from qqq_action_attribution import CAPITAL, account, action_outcomes, episodes, nav_path, relative_attribution


class QQQActionAttributionTests(unittest.TestCase):
    def fixture(self, count=80):
        dates = pd.bdate_range('2020-01-01', periods=count)
        t = np.arange(count)
        prices = {'QQQ': pd.DataFrame({'Open': 100*np.exp(.001*t+.02*np.sin(t)),
                                      'Close': 100*np.exp(.001*t+.02*np.cos(t))}, index=dates),
                  'BIL': pd.DataFrame({'Open': 100+.01*t, 'Close': 100.005+.01*t}, index=dates)}
        p = account({}, CAPITAL)
        for i, d in enumerate(dates):
            if i in (1, 2, 4, 7):
                w = {1: 1., 2: .3, 4: .7, 7: 1.}[i]
                p.rebalance({s: f.at[d, 'Open'] for s, f in prices.items()}, {'QQQ': w, 'BIL': 1-w}, date=d)
            p.record(d, {s: f.at[d, 'Close'] for s, f in prices.items()})
        return pd.DataFrame(p.history).set_index('Date'), prices

    def test_exact_attribution_includes_overnight_intraday_and_friction(self):
        history, prices = self.fixture()
        attr = relative_attribution(history, prices)
        components = attr[['GapAllocation', 'DayAllocation', 'Friction']].sum().sum()
        self.assertAlmostEqual(components, history.Portfolio.iloc[-1]/attr.PassiveQQQ.iloc[-1]-1, places=12)
        self.assertLess(attr.Friction.sum(), 0.)
        self.assertAlmostEqual(attr.AllocationGain.sum()+attr.AllocationDrag.sum(),
                               attr.GapAllocation.sum()+attr.DayAllocation.sum(), places=12)

    def test_counterfactual_clone_does_not_change_original_holdings(self):
        history, prices = self.fixture()
        prev = history.iloc[4]
        p = account(prev.Positions, prev.Cash)
        before = dict(p.positions), p.cash
        nav_path(p, prices, history.index[5:26], {'QQQ': 1., 'BIL': 0.})
        self.assertEqual((p.positions, p.cash), before)

    def test_label_requires_all_future_observations_and_starts_at_execution_open(self):
        history, prices = self.fixture()
        rows = action_outcomes(history, prices, 3, 4, .7, 'BUY', {}, 1)
        base = next(r for r in rows if r['Policy'] == 'FOLLOW_BASE' and r['Horizon'] == 21)
        before = account(history.iloc[3].Positions, history.iloc[3].Cash)
        start_nav = before.value({s: f.Open.iloc[4] for s, f in prices.items()})
        self.assertEqual(base['EndDate'], history.index[24])
        self.assertAlmostEqual(base['Return'], history.Portfolio.iloc[24]/start_nav-1)
        prefix = action_outcomes(history.iloc[:30], {s: f.iloc[:30] for s, f in prices.items()}, 3, 4, .7, 'BUY', {}, 1)
        self.assertEqual([r for r in prefix if r['Horizon'] == 21], [r for r in rows if r['Horizon'] == 21])
        self.assertEqual([r['Complete'] for r in prefix if r['Horizon'] == 63], [False])

    def test_censored_open_episode_and_close_boundary(self):
        dates = pd.bdate_range('2020-01-01', periods=7)
        target = pd.Series([1., .7, .5, 1., 1., .2, .1], index=dates)
        events = episodes(target)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['StartDecision'], dates[1])
        self.assertEqual(events[0]['EndDecision'], dates[3])
        self.assertTrue(events[0]['Closed'])
        self.assertFalse(events[1]['Closed'])
        prefix = episodes(target.iloc[:3])
        self.assertEqual(prefix[0]['StartDecision'], events[0]['StartDecision'])
        self.assertFalse(prefix[0]['Closed'])

    def test_fixed_hold_has_no_turnover_and_friction_cost_is_positive_for_reentry(self):
        history, prices = self.fixture()
        rows = action_outcomes(history, prices, 3, 4, .7, 'WEEKLY_REENTRY', {}, 1)
        hold = next(r for r in rows if r['Horizon'] == 21 and r['Policy'] == 'HOLD')
        full = next(r for r in rows if r['Horizon'] == 21 and r['Policy'] == 'FULL_REENTRY')
        self.assertEqual(hold['Costs'], 0.)
        self.assertGreater(full['Costs'], 0.)


if __name__ == '__main__':
    unittest.main()
