"""Guard priority, actual-account feedback, cancellation and execution clocks."""
from tests import PROJECT_ROOT
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
from portfolio import Portfolio
from qqq_guarded_reentry import FEATURES, ReentryPolicy, branch_path, forecast, queue_order, simulate
from qqq_guarded_size_control import StaticSizePolicy


class Source:
    def __init__(self):
        self.notification_context = {'state_values': {'trend_mode': 'RECOVERY', 'defense_mode': 'NORMAL'}}


class FeedbackSource(Source):
    def evaluate(self, date, market, portfolio):
        risk = portfolio.weights({'QQQ': market['QQQ']['Close'], 'BIL': market['BIL']['Close']}).get('QQQ', 0.)
        deep = risk > .75
        self.notification_context['state_values']['deep_guard'] = 'TRUE' if deep else 'FALSE'
        target = min(.2, risk) if deep else .7
        return {'target': {'QQQ': target, 'BIL': 1-target}, 'days': 1, 'rebalance': True}


class GuardedReentryTests(unittest.TestCase):
    def fixture(self):
        p = Portfolio()
        p.cash, p.positions = 0., {'QQQ': 70., 'BIL': 30.}
        market = {'QQQ': {'Close': 1., 'EMA20': .9, 'ROC5': 1.}, 'BIL': {'Close': 1.}}
        signal = {'target': {'QQQ': .7, 'BIL': .3}, 'rebalance': True, 'days': 1}
        return Source(), p, market, signal

    def test_each_hard_guard_cancels_an_active_overlay_and_preserves_target(self):
        for name, value in [('credit_guard', 'TRUE'), ('deep_guard', 'TRUE'), ('financial_guard', 'TRUE'),
                            ('structural_bear_mode', 'TRUE'), ('trend_mode', 'BEAR'), ('defense_mode', 'DEFENSE')]:
            with self.subTest(name=name):
                source, p, market, signal = self.fixture()
                source.notification_context['state_values'][name] = value
                signal['target'] = {'QQQ': .12, 'BIL': .88}  # Existing no-top-up target.
                policy = ReentryPolicy('RULE10')
                policy.active = True
                amended, blocked, _ = policy.amend(source, signal, market, p, permit_entry=True)
                self.assertTrue(blocked)
                self.assertFalse(policy.active)
                self.assertEqual(amended['target'], signal['target'])
                self.assertTrue(amended['rebalance'])

    def test_source_overweight_signal_does_not_churn_an_already_correct_overlay(self):
        source, p, market, signal = self.fixture()
        p.positions = {'QQQ': 80., 'BIL': 20.}
        policy = ReentryPolicy('RULE10')
        policy.active, policy.previous_target = True, {'QQQ': .8, 'BIL': .2}
        amended = policy.amend(source, signal, market, p)[0]
        self.assertAlmostEqual(amended['target']['QQQ'], .8)
        self.assertFalse(amended['rebalance'])

    def test_price_failure_returns_to_source_without_waiting_for_a_holding_period(self):
        source, p, market, signal = self.fixture()
        policy = ReentryPolicy('RULE10')
        policy.amend(source, signal, market, p, permit_entry=True)
        market['QQQ']['ROC5'] = -1.
        signal['rebalance'] = False
        amended = policy.amend(source, signal, market, p)[0]
        self.assertFalse(policy.active)
        self.assertEqual(amended['target'], signal['target'])
        self.assertTrue(amended['rebalance'])

    def test_identical_delayed_target_keeps_deadline_and_hard_target_replaces_it(self):
        signal = self.fixture()[-1]
        pending = queue_order(None, signal, 10, 1)
        self.assertEqual(pending['Due'], 12)
        repeated = queue_order(pending, signal, 11, 1)
        self.assertEqual(repeated['Due'], 12)
        signal['target'] = {'QQQ': .2, 'BIL': .8}
        replaced = queue_order(repeated, signal, 11, 1)
        self.assertEqual(replaced['Due'], 13)
        self.assertEqual(replaced['Target']['QQQ'], .2)

    def test_source_evaluates_changed_actual_holdings_in_the_next_session(self):
        _, _, market, _ = self.fixture()
        dates = pd.bdate_range('2020-01-06', periods=4)
        data = [{'Date': d, 'Open': {'QQQ': 1., 'BIL': 1.}, 'Close': {'QQQ': 1., 'BIL': 1.}, 'Market': market} for d in dates]
        with patch('qqq_guarded_reentry.make_source', side_effect=lambda *args: FeedbackSource()):
            history = simulate(data, 30, 'CURRENT', 'RULE10')[0]
        self.assertTrue(history.Active.iloc[0])
        self.assertEqual(history.SourceTarget.iloc[1], .2)
        self.assertEqual(history.Target.iloc[1], .2)
        self.assertFalse(history.Active.iloc[1])
        self.assertLessEqual(history.QQQActual.iloc[2], .2001)

    def test_insufficient_completed_episodes_produce_no_model_decision(self):
        prediction, rows, events = forecast(pd.DataFrame(), pd.Timestamp('2020-01-01'), {})
        self.assertIsNone(prediction)
        self.assertEqual((rows, events), (0, 0))

    def test_model_requires_both_episode_and_action_outcome_to_have_ended(self):
        date = pd.Timestamp('2025-01-01')
        labels = pd.DataFrame([{'Episode': e, 'EpisodeEnd': date-pd.Timedelta(days=10),
                                'EndDate': date-pd.Timedelta(days=1), 'DeltaToBase': .01,
                                **{f: float(i+e) for f in FEATURES}}
                               for e in range(4) for i in range(3)])
        values = {f: 1. for f in FEATURES}
        prediction, _, events = forecast(labels, date, values)
        self.assertEqual(events, 4)
        self.assertAlmostEqual(prediction, .01)
        labels.loc[labels.Episode == 3, 'EndDate'] = date+pd.Timedelta(days=1)
        self.assertIsNone(forecast(labels, date, values)[0])
        labels['EndDate'] = date-pd.Timedelta(days=1)
        labels.loc[labels.Episode == 3, 'EpisodeEnd'] = date+pd.Timedelta(days=1)
        self.assertIsNone(forecast(labels, date, values)[0])

    def test_branch_retains_an_existing_pending_order_if_today_has_no_new_order(self):
        _, p, market, signal = self.fixture()
        signal['rebalance'] = False
        dates = pd.bdate_range('2020-01-06', periods=22)
        data = []
        for i, d in enumerate(dates):
            q_open, q_close = (1. if i <= 1 else 2.), (1. if i == 0 else 2.)
            observations = {'QQQ': {'Close': q_close, 'EMA20': .9, 'ROC5': 1.}, 'BIL': {'Close': 1.}}
            data.append({'Date': d, 'Open': {'QQQ': q_open, 'BIL': 1.},
                         'Close': {'QQQ': q_close, 'BIL': 1.}, 'Market': observations})
        snapshot = {'Index': 0, 'Source': FeedbackSource(), 'Signal': signal, 'Portfolio': p,
                    'Pending': {'Due': 1, 'Target': {'QQQ': .5, 'BIL': .5}, 'Decision': -1, 'Reason': 'OLDER'}}
        result = branch_path(data, snapshot)
        self.assertAlmostEqual(result['Path'][0], 1.5)

    def test_static_diagnostic_changes_price_gate_only_and_still_obeys_guards(self):
        source, p, market, signal = self.fixture()
        market['QQQ']['ROC5'] = -1.
        policy = StaticSizePolicy('RULE10')
        amended, _, _ = policy.amend(source, signal, market, p)
        self.assertAlmostEqual(amended['target']['QQQ'], .8)
        source.notification_context['state_values']['deep_guard'] = 'TRUE'
        signal['target'] = {'QQQ': .12, 'BIL': .88}
        amended, blocked, _ = policy.amend(source, signal, market, p)
        self.assertTrue(blocked)
        self.assertFalse(policy.active)
        self.assertEqual(amended['target'], signal['target'])


if __name__ == '__main__':
    unittest.main()
