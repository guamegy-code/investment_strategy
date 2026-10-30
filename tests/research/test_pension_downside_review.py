"""Sequential defense, unavailable data, and base-target priority checks."""
from tests import PROJECT_ROOT
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT / "src/legacy-python/validation"))
from pension_downside_review import DownsideOverlay, StableDelayBacktest, cap_schedule
from portfolio import Portfolio


class DummyBase:
    def __init__(self, target):
        self.target = target
        self.notification_context = {"target_weights": {"QQQ": target, "BIL": 1-target}}

    def evaluate(self, *args):
        return {"target": {"QQQ": self.target, "BIL": 1-self.target},
                "rebalance": False, "days": 1}


class DummyPortfolio:
    def __init__(self, weight):
        self.weight = weight

    def weights(self, _):
        return {"QQQ": self.weight, "BIL": 1-self.weight}


class PensionDownsideTests(unittest.TestCase):
    def test_repeated_pending_order_executes_on_original_deadline(self):
        engine = StableDelayBacktest.__new__(StableDelayBacktest)
        engine.strategy = DummyBase(1.)
        engine.signal_delay_days = 1
        engine.bear_signal_delay_days = None
        engine.delayed_rebalance = None
        engine.portfolio = Portfolio()
        signal = {"target": {"QQQ": .8, "BIL": .2}, "days": 1}
        dates = pd.bdate_range("2020-01-01", periods=3)
        engine._queue_rebalance(signal, dates[0])
        engine._activate_delayed_rebalance(dates[1])
        engine._queue_rebalance(signal, dates[1])
        engine._activate_delayed_rebalance(dates[2])
        self.assertEqual(engine.portfolio.pending_target, signal["target"])
        self.assertIsNone(engine.delayed_rebalance)

    def features(self):
        dates = pd.bdate_range("2020-01-01", periods=5)
        return pd.DataFrame({"qqq_return5": [-.04, -.01, .01, .02, .03],
            "close": [90, 95, 101, 102, 103], "qqq_ema20": [100]*5,
            "vix9d_vix": [1.1, 1.1, 1., 1., 1.],
            "vix_vix3m": [1.1, 1.1, .9, .9, .9], "long_bear": [False]*5}, index=dates)

    def test_two_step_recovery_and_prefix_invariance(self):
        frame = self.features()
        result = cap_schedule(frame, "TERM_FAST")
        self.assertEqual(result.Cap.tolist(), [.8, .8, .9, 1., 1.])
        pd.testing.assert_frame_equal(result.iloc[:3], cap_schedule(frame.iloc[:3], "TERM_FAST"))

    def test_missing_observation_does_not_release_defense(self):
        frame = self.features()
        frame.loc[frame.index[3], "vix9d_vix"] = float("nan")
        result = cap_schedule(frame, "TERM_FAST")
        self.assertEqual(result.Cap.tolist(), [.8, .8, .9, .8, .9])
        self.assertFalse(result.Available.iloc[3])

    def test_price_control_uses_same_price_threshold_without_vol_gate(self):
        frame = self.features()
        frame["vix9d_vix"] = .9
        self.assertEqual(cap_schedule(frame, "PRICE_SLOW").Cap.iloc[0], .8)
        self.assertEqual(cap_schedule(frame, "TERM_SLOW").Cap.iloc[0], 1.)

    def test_price_reentry_does_not_wait_for_volatility_normalization(self):
        frame = self.features()
        frame["vix9d_vix"] = 1.1
        self.assertEqual(cap_schedule(frame, "TERM_FAST").Cap.tolist(), [.8]*5)
        self.assertEqual(cap_schedule(frame, "TERM_PRICE_FAST").Cap.tolist(), [.8, .8, .9, 1., 1.])

    def test_failed_reentry_memory_applies_only_in_long_bear(self):
        frame = self.features()
        next_date = frame.index[-1] + pd.offsets.BDay()
        frame.loc[next_date] = frame.iloc[0]
        for long_bear, expected in ((True, .65), (False, .8)):
            frame.loc[next_date, "long_bear"] = long_bear
            result = cap_schedule(frame, "TERM_MEMORY_FAST")
            self.assertAlmostEqual(result.Cap.iloc[-1], expected)

    def test_stronger_defense_and_no_topup_are_preserved(self):
        schedule = cap_schedule(self.features(), "TERM_FAST")
        for weight in (0., .17, .2, .35, .5, .7):
            overlay = DownsideOverlay(DummyBase(weight), schedule)
            for date in schedule.index:
                result = overlay.evaluate(date, {"QQQ": {"Close": 100}, "BIL": {"Close": 100}},
                                          DummyPortfolio(weight))
                self.assertAlmostEqual(result["target"]["QQQ"], weight)
                self.assertFalse(result["rebalance"])

    def test_cap_transition_trades_and_idle_base_does_not(self):
        schedule = cap_schedule(self.features(), "TERM_FAST")
        overlay = DownsideOverlay(DummyBase(1.), schedule)
        first = overlay.evaluate(schedule.index[0], {"QQQ": {"Close": 100}, "BIL": {"Close": 100}},
                                 DummyPortfolio(1.))
        self.assertTrue(first["rebalance"])
        self.assertEqual(first["target"], {"QQQ": .8, "BIL": 1-.8})
        idle = DownsideOverlay(DummyBase(1.), cap_schedule(self.features(), "BASE"))
        self.assertFalse(idle.evaluate(schedule.index[0],
            {"QQQ": {"Close": 100}, "BIL": {"Close": 100}}, DummyPortfolio(1.))["rebalance"])


if __name__ == "__main__":
    unittest.main()
