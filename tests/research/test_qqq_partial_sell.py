"""Partial-sale sizing, native handoff and causal invalidation execution."""
from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import pandas as pd

from tests import PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
import qqq_partial_sell as study
from tests.research.test_qqq_memory_sell import fixture
from tests.research.test_qqq_guarded_reentry import FeedbackSource


class PartialSellTests(unittest.TestCase):
    def test_half_sale_only_reduces_half_the_gap_and_never_tops_up_lower_protection(self):
        for native, expected in [(1., .65), (.7, .5), (.3, .3), (.12, .12), (0., 0.)]:
            with self.subTest(native=native):
                source, p, market, signal = fixture()
                signal['target'] = {'QQQ': native, 'BIL': 1-native}
                result = study.PartialSellPolicy('HALF_FAST').amend(source, signal, market, p)[0]
                self.assertAlmostEqual(result['target']['QQQ'], expected)

    def test_sizing_is_not_recursive_and_does_not_churn_at_the_final_target(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.amend(source, signal, market, p)
        p.positions = {'QQQ': 50., 'BIL': 50.}
        for _ in range(3):
            result = policy.amend(source, signal, market, p)[0]
            self.assertAlmostEqual(result['target']['QQQ'], .5)
            self.assertFalse(result['rebalance'])

    def test_broken_native_candidate_releases_fast_only_and_keeps_memory(self):
        source, p, market, signal = fixture()
        fast, slow = study.PartialSellPolicy('HALF_FAST'), study.PartialSellPolicy('HALF_SLOW')
        for policy in (fast, slow):
            policy.amend(source, signal, market, p)
        source._state_candidates.clear()
        market['QQQ']['Close'] = .8  # Below EMA20: the old recovery gate is still false.
        before = deepcopy(vars(source))
        result = fast.amend(source, signal, market, p)[0]
        slow.amend(source, signal, market, p)
        self.assertEqual(fast.early.event, 'INVALIDATION_RELEASE')
        self.assertFalse(fast.early.active)
        self.assertTrue(slow.early.active)
        self.assertEqual(result['target'], signal['target'])
        self.assertEqual(vars(source), before)

    def test_losing_disagreement_alone_does_not_release_or_reset_confirmation(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.amend(source, signal, market, p)
        source.down_days = 0
        source._state_candidates['defense_mode']['days'] = 3
        policy.amend(source, signal, market, p)
        self.assertTrue(policy.early.active)
        self.assertEqual(source._state_candidates['defense_mode']['days'], 3)

    def test_native_defense_handoff_uses_full_native_target_before_fast_invalidation(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.amend(source, signal, market, p)
        source.notification_context['state_values']['defense_mode'] = 'DEFENSE'
        source._state_candidates.clear()
        signal['target'] = {'QQQ': .3, 'BIL': .7}
        result, blocked, _ = policy.amend(source, signal, market, p, permit_entry=True)
        self.assertTrue(blocked)
        self.assertEqual(policy.early.event, 'HANDOFF')
        self.assertEqual(result['target'], signal['target'])
        self.assertFalse(policy.reentry.active)

    def test_invalidation_restores_only_the_current_guarded_target(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.amend(source, signal, market, p)
        source._state_candidates.clear()
        source.notification_context['state_values']['deep_guard'] = 'TRUE'
        signal['target'] = {'QQQ': .12, 'BIL': .88}
        result, blocked, _ = policy.amend(source, signal, market, p, permit_entry=True)
        self.assertTrue(blocked)
        self.assertEqual(result['target'], signal['target'])
        self.assertFalse(policy.reentry.active)

    def test_rearming_requires_two_new_native_confirmations(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.amend(source, signal, market, p)
        source._state_candidates.clear()
        policy.amend(source, signal, market, p)
        source._state_candidates['defense_mode'] = {'value': 'DEFENSE', 'days': 1}
        policy.amend(source, signal, market, p)
        self.assertFalse(policy.early.active)
        source._state_candidates['defense_mode']['days'] = 2
        policy.amend(source, signal, market, p)
        self.assertTrue(policy.early.active)
        self.assertEqual(policy.early.event, 'ENTRY')

    def test_partial_sale_cancels_an_existing_method_one_addition(self):
        source, p, market, signal = fixture()
        policy = study.PartialSellPolicy('HALF_FAST')
        policy.reentry.active = True
        result, _, gate = policy.amend(source, signal, market, p, permit_entry=True)
        self.assertTrue(gate)
        self.assertFalse(policy.reentry.active)
        self.assertAlmostEqual(result['target']['QQQ'], .5)

    def test_invalidation_is_executed_on_the_next_open_or_delayed_open(self):
        class Sequence:
            down_days = 2
            up_days = 0
            accelerated = []
            variables = {'recovery_score': 0}

            def __init__(self):
                self.count = 0
                self.notification_context = {'state_values': {'trend_mode': 'CAUTION', 'defense_mode': 'WARNING'}}
                self._state_candidates = {}

            def evaluate(self, date, market, portfolio):
                self._state_candidates = {'defense_mode': {'value': 'DEFENSE', 'days': 2}} if self.count == 0 else {}
                self.count += 1
                return {'target': {'QQQ': 1., 'BIL': 0.}, 'rebalance': True, 'days': 1}

        _, _, market, _ = fixture()
        data = [{'Date': d, 'Market': market, 'Open': {'QQQ': 1., 'BIL': 1.},
                 'Close': {'QQQ': 1., 'BIL': 1.}} for d in pd.bdate_range('2020-01-06', periods=5)]
        for delay in (0, 1):
            with self.subTest(delay=delay), patch.object(study, 'make_source', side_effect=lambda *args: Sequence()):
                h, _ = study.simulate(data, 30, 'CURRENT', 'HALF_FAST', delay=delay)
                self.assertEqual(h.EarlyEvent.iloc[1], 'INVALIDATION_RELEASE')
                self.assertAlmostEqual(h.Target.iloc[1], 1.)
                self.assertAlmostEqual(h.QQQActual.iloc[1], .65, places=3)
                self.assertAlmostEqual(h.QQQActual.iloc[2+delay], 1., places=3)
                if delay:
                    self.assertAlmostEqual(h.QQQActual.iloc[2], .65, places=3)

    def test_runner_evaluates_changed_actual_holdings(self):
        class Feedback(FeedbackSource):
            down_days = up_days = 0
            accelerated = []
            _state_candidates = {}
        _, _, market, _ = fixture()
        data = [{'Date': d, 'Market': market, 'Open': {'QQQ': 1., 'BIL': 1.},
                 'Close': {'QQQ': 1., 'BIL': 1.}} for d in pd.bdate_range('2020-01-06', periods=4)]
        with patch.object(study, 'make_source', side_effect=lambda *args: Feedback()):
            h, _ = study.simulate(data, 30, 'CURRENT', 'HALF_FAST')
        self.assertAlmostEqual(h.Target.iloc[0], .85)
        self.assertAlmostEqual(h.SourceTarget.iloc[1], .2)
        self.assertFalse(h.Active.iloc[1])


if __name__ == '__main__':
    unittest.main()
