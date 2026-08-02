import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multi_market_validation import (  # noqa: E402
    MultiMarketDownsideStrategy,
    PROFILES,
)


def equity(score):
    return {
        "Close": 100.0 + score * 15.0,
        "EMA200": 100.0,
        "ROC60": score * 15.0,
        "ROC120": score * 25.0,
        "ROC252": score * 40.0,
        "VOL60": 0.20,
    }


def safe_asset():
    return {
        "Close": 100.0,
        "ROC60": 2.0,
        "ROC120": 3.0,
        "VOL60": 0.05,
    }


def market(qqq, spy, iwm):
    return {
        "QQQ": equity(qqq),
        "SPY": equity(spy),
        "IWM": equity(iwm),
        "BND": safe_asset(),
        "BIL": safe_asset(),
        "GLD": safe_asset(),
    }


class MultiMarketValidationTests(unittest.TestCase):
    @staticmethod
    def strategy(name):
        profile = next(profile for profile in PROFILES if profile.name == name)
        return MultiMarketDownsideStrategy(profile)

    def test_two_of_three_ignores_unconfirmed_qqq_weakness(self):
        strategy = self.strategy("MULTI_MARKET_2_OF_3")

        weight = strategy._desired_target(market(-0.8, 0.5, 0.5))["QQQ"]

        self.assertEqual(weight, 0.70)

    def test_two_of_three_reduces_when_another_market_confirms(self):
        strategy = self.strategy("MULTI_MARKET_2_OF_3")

        weight = strategy._desired_target(market(-0.8, -0.5, 0.5))["QQQ"]

        self.assertLess(weight, 0.70)

    def test_all_three_requires_every_market_to_be_negative(self):
        strategy = self.strategy("MULTI_MARKET_ALL_3")

        partial = strategy._desired_target(market(-0.8, -0.5, 0.5))["QQQ"]
        unanimous = strategy._desired_target(market(-0.8, -0.5, -0.3))["QQQ"]

        self.assertEqual(partial, 0.70)
        self.assertLess(unanimous, partial)

    def test_graded_confirmation_reduces_more_with_broader_weakness(self):
        strategy = self.strategy("MULTI_MARKET_GRADED")

        narrow = strategy._desired_target(market(-0.8, 0.5, 0.5))["QQQ"]
        broad = strategy._desired_target(market(-0.8, -0.5, -0.3))["QQQ"]

        self.assertLess(broad, narrow)


if __name__ == "__main__":
    unittest.main()
