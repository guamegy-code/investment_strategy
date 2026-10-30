"""Causal confirmation switching, protection invariance and closed-loop execution."""
from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests import PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT/'src/legacy-python/validation'))
import qqq_adaptive_speed as study
from portfolio import Portfolio
from strategy_dsl import DeclarativeStrategy
from tests.research.test_qqq_guarded_reentry import FeedbackSource


def fixture(initial='BEAR', defense='NORMAL', speed='BOTH'):
    definition = {
        'strategy': {'id': 'speed-test', 'name': 'Speed test', 'version': 1, 'dsl_version': 1},
        'assets': {'required': ['QQQ', 'BIL'], 'risk': ['QQQ']},
        'state': {
            'trend_mode': {'initial': initial, 'rules': [
                {'when': "state.trend_mode == 'BEAR' and QQQ.roc5 > 0", 'set': 'RECOVERY', 'confirm': 3},
                {'when': "state.trend_mode == 'RECOVERY' and QQQ.roc20 > 0", 'set': 'BULL', 'confirm': 3},
                {'when': "state.trend_mode == 'BULL' and QQQ.roc5 < 0", 'set': 'BEAR', 'confirm': 10}]},
            'defense_mode': {'initial': defense, 'rules': [
                {'when': "state.defense_mode == 'WARNING' and QQQ.roc5 < 0", 'set': 'DEFENSE', 'confirm': 6},
                {'when': "state.defense_mode == 'DEFENSE' and QQQ.roc5 > 0", 'set': 'NORMAL', 'confirm': 2}]}
        },
        'target': [{'when': "state.trend_mode == 'BEAR'", 'weights': {'QQQ': '0%', 'BIL': '100%'}},
                   {'weights': {'QQQ': '80%', 'BIL': '20%'}}],
        'rebalance': [{'check': 'daily', 'when': "changed(state.trend_mode)"}],
        'execution': {'days': 1}
    }
    source = study.AdaptiveSource(DeclarativeStrategy(definition), speed)
    p = Portfolio()
    p.cash = 100.
    market = {'QQQ': {'Close': 1., 'ROC5': 1., 'ROC20': 1., 'TurnFastExcess': .02, 'TurnSlowExcess': -.10},
              'BIL': {'Close': 1.}}
    return source, p, market


class AdaptiveSpeedTests(unittest.TestCase):
    def test_buy_acceleration_requires_two_consecutive_disagreement_sessions(self):
        source, p, market = fixture()
        source.evaluate(pd.Timestamp('2020-01-02'), market, p)
        self.assertEqual(source._state_values['trend_mode'], 'BEAR')
        self.assertFalse(source.buy_fast)
        source.evaluate(pd.Timestamp('2020-01-03'), market, p)
        self.assertEqual(source._state_values['trend_mode'], 'RECOVERY')
        self.assertEqual(source.accelerated, ['BUY:trend_mode:BEAR->RECOVERY'])

    def test_disagreement_never_replaces_the_native_entry_condition(self):
        source, p, market = fixture()
        market['QQQ']['ROC5'] = -1.
        for date in pd.bdate_range('2020-01-02', periods=5):
            source.evaluate(date, market, p)
        self.assertTrue(source.buy_fast)
        self.assertEqual(source._state_values['trend_mode'], 'BEAR')

    def test_missing_or_zero_signal_resets_streak_and_restores_confirmation(self):
        source, p, market = fixture()
        market['QQQ']['ROC5'] = -1.
        dates = pd.bdate_range('2020-01-02', periods=5)
        for d in dates[:2]:
            source.evaluate(d, market, p)
        self.assertTrue(source.buy_fast)
        market['QQQ']['TurnSlowExcess'] = np.nan
        source.evaluate(dates[2], market, p)
        self.assertFalse(source.buy_fast)
        self.assertEqual(source.up_days, 0)
        self.assertEqual(source.definition['state']['trend_mode']['rules'][0]['confirm'], 3)
        market['QQQ']['TurnSlowExcess'] = 0.
        source.evaluate(dates[3], market, p)
        self.assertFalse(source.buy_fast)

    def test_accelerating_sell_preserves_native_candidate_days(self):
        source, p, market = fixture(initial='BULL', defense='WARNING', speed='SELL')
        market['QQQ'].update(ROC5=-1., TurnFastExcess=-.02, TurnSlowExcess=.1)
        dates = pd.bdate_range('2020-01-02', periods=3)
        source.evaluate(dates[0], market, p)
        self.assertEqual(source._state_values['defense_mode'], 'WARNING')
        source.evaluate(dates[1], market, p)
        self.assertEqual(source._state_values['defense_mode'], 'DEFENSE')
        self.assertEqual(source._state_values['trend_mode'], 'BULL')
        source.evaluate(dates[2], market, p)
        self.assertEqual(source._state_values['trend_mode'], 'BEAR')
        self.assertFalse(source.buy_fast)

    def test_protections_and_valuation_release_are_unchanged(self):
        for number in (30, 32):
            source = study.make_source(number, 'CURRENT', 'BOTH')
            before = deepcopy(source.definition)
            _, p, market = fixture()
            market['QQQ'].update(TurnFastExcess=-.02, TurnSlowExcess=.1)
            # Exercise the real rule mutations without requiring a complete market fixture.
            with patch.object(source.source, 'evaluate', return_value={}):
                for d in pd.bdate_range('2020-01-02', periods=2):
                    source.evaluate(d, market, p)
            for name in study.PROTECTED:
                self.assertEqual(source.definition['state'].get(name), before['state'].get(name))
            self.assertEqual(source.definition['target'], before['target'])
            self.assertNotEqual(source.definition['state']['trend_mode'], before['state']['trend_mode'])
            self.assertTrue(all(name not in study.PROTECTED for name, *_ in source.rules))
            self.assertTrue(all(not (name == 'defense_mode' and rule['set'] == 'NORMAL')
                                for name, rule, *_ in source.rules))
        source, p, market = fixture(defense='DEFENSE', speed='ALWAYS')
        source.evaluate(pd.Timestamp('2020-01-02'), market, p)
        self.assertEqual(source._state_values['defense_mode'], 'DEFENSE')
        source.evaluate(pd.Timestamp('2020-01-03'), market, p)
        self.assertEqual(source._state_values['defense_mode'], 'NORMAL')

    def test_always_fast_control_removes_only_the_disagreement_gate(self):
        source, p, market = fixture(speed='ALWAYS')
        market['QQQ']['TurnSlowExcess'] = .10
        source.evaluate(pd.Timestamp('2020-01-02'), market, p)
        self.assertEqual(source._state_values['trend_mode'], 'RECOVERY')
        self.assertTrue(source.buy_fast)
        ordinary, p, market = fixture(speed='BOTH')
        market['QQQ']['TurnSlowExcess'] = .10
        ordinary.evaluate(pd.Timestamp('2020-01-02'), market, p)
        self.assertEqual(ordinary._state_values['trend_mode'], 'BEAR')

    def test_native_immediate_transition_is_not_mislabeled_as_accelerated(self):
        source, p, market = fixture(initial='BULL', speed='BOTH')
        source.definition['state']['defense_mode']['rules'].insert(0,
            {'when': "state.defense_mode == 'NORMAL' and QQQ.roc5 < -5", 'set': 'DEFENSE'})
        market['QQQ'].update(TurnFastExcess=-.02, TurnSlowExcess=.1)
        source.evaluate(pd.Timestamp('2020-01-02'), market, p)
        market['QQQ']['ROC5'] = -10.
        source.evaluate(pd.Timestamp('2020-01-03'), market, p)
        self.assertEqual(source._state_values['defense_mode'], 'DEFENSE')
        self.assertNotIn('SELL:defense_mode:NORMAL->DEFENSE', source.accelerated)

    def test_features_use_prior_prices_and_keep_missing_observations(self):
        dates = pd.bdate_range('2018-01-01', periods=400)
        q = pd.DataFrame({'Close': np.exp(np.arange(400)*.001)}, index=dates)
        b = pd.DataFrame({'Close': np.exp(np.arange(400)*.0001)}, index=dates)
        features = study.momentum_features(q, b)
        self.assertAlmostEqual(features.TurnFastExcess.iloc[-1], np.exp(.02)-np.exp(.002))
        self.assertTrue(features.TurnSlowExcess.iloc[:252].isna().all())
        pd.testing.assert_frame_equal(features.iloc[:310], study.momentum_features(q.iloc[:310], b.iloc[:310]))
        q.loc[dates[300], 'Close'] = np.nan
        self.assertTrue(pd.isna(study.momentum_features(q, b).TurnFastExcess.iloc[300]))

    def test_combined_runner_feeds_actual_reentry_holdings_to_next_source_decision(self):
        class Feedback(FeedbackSource):
            down_days = up_days = 0
            sell_fast = buy_fast = False
            accelerated = []
        market = {'QQQ': {'Close': 1., 'EMA20': .9, 'ROC5': 1.}, 'BIL': {'Close': 1.}}
        data = [{'Date': d, 'Market': market, 'Open': {'QQQ': 1., 'BIL': 1.},
                 'Close': {'QQQ': 1., 'BIL': 1.}} for d in pd.bdate_range('2020-01-06', periods=4)]
        with patch.object(study, 'make_source', side_effect=lambda *args: Feedback()):
            history, _ = study.simulate(data, 30, 'CURRENT', 'COMBINED')
        self.assertAlmostEqual(history.Target.iloc[0], .85)
        self.assertTrue(history.Active.iloc[0])
        self.assertAlmostEqual(history.Target.iloc[1], .2)
        self.assertFalse(history.Active.iloc[1])
        self.assertLessEqual(history.QQQActual.iloc[2], .2001)


if __name__ == '__main__':
    unittest.main()
