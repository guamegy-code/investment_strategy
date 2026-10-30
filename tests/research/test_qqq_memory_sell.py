"""Independent early selling must retain native memory and protection priority."""
from copy import deepcopy
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import pandas as pd

from tests import PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
import qqq_memory_sell as study
from portfolio import Portfolio
from tests.research.test_qqq_guarded_reentry import FeedbackSource


def fixture():
    source = SimpleNamespace(
        notification_context={'state_values': {'trend_mode': 'CAUTION', 'defense_mode': 'WARNING',
                                               'valuation_peak': 75.2}},
        _state_candidates={'defense_mode': {'value': 'DEFENSE', 'days': 2}},
        variables={'recovery_score': 0}, down_days=2)
    p = Portfolio()
    p.cash, p.positions = 0., {'QQQ': 70., 'BIL': 30.}
    market = {'QQQ': {'Close': 1., 'EMA20': .9, 'ROC5': 1.}, 'BIL': {'Close': 1.}}
    signal = {'target': {'QQQ': .7, 'BIL': .3}, 'rebalance': True, 'days': 1}
    return source, p, market, signal


class MemorySellTests(unittest.TestCase):
    def test_entry_requires_native_candidate_and_disagreement_confirmations(self):
        for candidate, streak, expected in [({}, 5, False),
                ({'value': 'DEFENSE', 'days': 1}, 5, False),
                ({'value': 'NORMAL', 'days': 3}, 5, False),
                ({'value': 'DEFENSE', 'days': 2}, 1, False),
                ({'value': 'DEFENSE', 'days': 2}, 2, True)]:
            with self.subTest(candidate=candidate, streak=streak):
                source, _, market, _ = fixture()
                source._state_candidates['defense_mode'] = candidate
                source.down_days = streak
                before = deepcopy(vars(source))
                self.assertEqual(study.EarlyValuationSell(True).evaluate(source, market), expected)
                self.assertEqual(vars(source), before)

    def test_recovery_needs_two_consecutive_sessions_and_keeps_native_peak(self):
        source, _, market, _ = fixture()
        policy = study.EarlyValuationSell(True)
        self.assertTrue(policy.evaluate(source, market))
        source.down_days = 0  # Losing the entry gate alone is not a release.
        self.assertTrue(policy.evaluate(source, market))
        source.variables['recovery_score'] = 4
        self.assertTrue(policy.evaluate(source, market))
        market['QQQ']['Close'] = .8
        self.assertTrue(policy.evaluate(source, market))
        self.assertEqual(policy.recovery_days, 0)
        market['QQQ']['Close'] = 1.
        self.assertTrue(policy.evaluate(source, market))
        before = deepcopy(vars(source))
        self.assertFalse(policy.evaluate(source, market))
        self.assertEqual(policy.event, 'RECOVERY_RELEASE')
        self.assertEqual(vars(source), before)

    def test_native_defense_handoff_does_not_rebalance_an_already_correct_account(self):
        source, p, market, signal = fixture()
        policy = study.MemorySellPolicy(True)
        policy.amend(source, signal, market, p)
        p.positions = {'QQQ': 30., 'BIL': 70.}
        source.notification_context['state_values']['defense_mode'] = 'DEFENSE'
        signal['target'] = {'QQQ': .3, 'BIL': .7}
        amended, blocked, _ = policy.amend(source, signal, market, p, permit_entry=True)
        self.assertTrue(blocked)
        self.assertFalse(policy.early.active)
        self.assertEqual(policy.early.event, 'HANDOFF')
        self.assertEqual(amended['target'], signal['target'])
        self.assertFalse(amended['rebalance'])

    def test_native_clear_releases_cap_without_forcing_out_of_schedule_reentry(self):
        source, p, market, signal = fixture()
        policy = study.MemorySellPolicy(True)
        policy.amend(source, signal, market, p)
        source.notification_context['state_values']['defense_mode'] = 'NORMAL'
        amended = policy.amend(source, signal, market, p, permit_entry=False)[0]
        self.assertEqual(policy.early.event, 'NATIVE_CLEAR')
        self.assertFalse(policy.reentry.active)
        self.assertEqual(amended['target'], signal['target'])
        self.assertTrue(amended['rebalance'])

    def test_early_sell_cancels_existing_reentry_even_with_positive_price_gate(self):
        source, p, market, signal = fixture()
        policy = study.MemorySellPolicy(True)
        policy.reentry.active = True
        amended, _, gate = policy.amend(source, signal, market, p, permit_entry=True)
        self.assertTrue(gate)
        self.assertFalse(policy.reentry.active)
        self.assertAlmostEqual(amended['target']['QQQ'], .3)
        self.assertTrue(amended['rebalance'])

    def test_lower_protection_and_no_topup_targets_win_over_the_cap(self):
        for name in ('credit_guard', 'deep_guard', 'financial_guard', 'structural_bear_mode'):
            with self.subTest(guard=name):
                source, p, market, signal = fixture()
                source.notification_context['state_values'][name] = 'TRUE'
                signal['target'] = {'QQQ': .12, 'BIL': .88}
                policy = study.MemorySellPolicy(True)
                amended, blocked, _ = policy.amend(source, signal, market, p, permit_entry=True)
                self.assertTrue(blocked)
                self.assertTrue(policy.early.active)
                self.assertEqual(amended['target'], signal['target'])
                self.assertFalse(policy.reentry.active)

    def test_native_overweight_order_does_not_churn_at_the_capped_target(self):
        source, p, market, signal = fixture()
        policy = study.MemorySellPolicy(True)
        policy.amend(source, signal, market, p)
        p.positions = {'QQQ': 30., 'BIL': 70.}
        amended = policy.amend(source, signal, market, p)[0]
        self.assertEqual(amended['target'], {'QQQ': .3, 'BIL': .7})
        self.assertFalse(amended['rebalance'])
        p.positions = {'QQQ': 20., 'BIL': 80.}
        self.assertTrue(policy.amend(source, signal, market, p)[0]['rebalance'])

    def test_all_profiles_leave_native_valuation_and_guard_definitions_unchanged(self):
        for number in (30, 32):
            for profile in study.LOCK['profiles']:
                with self.subTest(source=number, profile=profile):
                    source = study.make_source(number, 'CURRENT', profile)
                    before = deepcopy(source.definition)
                    _, p, market, _ = fixture()
                    market['QQQ'].update(TurnFastExcess=-.02, TurnSlowExcess=.1)
                    with patch.object(source.source, 'evaluate', return_value={}):
                        for d in pd.bdate_range('2020-01-02', periods=3):
                            source.evaluate(d, market, p)
                    for name in (*study.adaptive.PROTECTED, 'defense_mode', 'valuation_peak'):
                        self.assertEqual(source.definition['state'].get(name), before['state'].get(name))
                    self.assertFalse(any(name == 'defense_mode' for name, *_ in source.rules))

    def test_runner_passes_changed_actual_holdings_to_native_source(self):
        class Feedback(FeedbackSource):
            down_days = up_days = 0
            accelerated = []
            _state_candidates = {}
        _, _, market, _ = fixture()
        data = [{'Date': d, 'Market': market, 'Open': {'QQQ': 1., 'BIL': 1.},
                 'Close': {'QQQ': 1., 'BIL': 1.}} for d in pd.bdate_range('2020-01-06', periods=4)]
        with patch.object(study, 'make_source', side_effect=lambda *args: Feedback()):
            history, _ = study.simulate(data, 30, 'CURRENT', 'MEMORY_BOTH')
        self.assertAlmostEqual(history.Target.iloc[0], .85)
        self.assertAlmostEqual(history.SourceTarget.iloc[1], .2)
        self.assertFalse(history.Active.iloc[1])
        self.assertLessEqual(history.QQQActual.iloc[2], .2001)


if __name__ == '__main__':
    unittest.main()
