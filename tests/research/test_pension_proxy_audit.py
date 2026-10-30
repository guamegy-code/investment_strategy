"""Guard against invented overlap and future observations in proxy diagnostics."""
from tests import PROJECT_ROOT
import unittest

import numpy as np
import pandas as pd

from validation.pension_proxy_audit import aligned_returns, observed_prices, proxy_diagnostics, validate_prices


class PensionProxyAuditTests(unittest.TestCase):
    def test_dividend_only_row_is_reported_without_inventing_a_price(self):
        dates = pd.bdate_range("2025-01-01", periods=3)
        raw = pd.DataFrame({"Open": [100., np.nan, 105.], "Close": [100., np.nan, 105.],
                            "Dividends": [0., 2., 0.]}, index=dates)
        clean, missing = observed_prices(raw, "dividend gap")
        self.assertEqual(missing, [str(dates[1].date())])
        self.assertEqual(list(clean.index), list(dates[[0, 2]]))
        self.assertTrue(pd.isna(raw.loc[dates[1], "Close"]))
        self.assertEqual(raw.loc[dates[1], "Dividends"], 2.)

    def test_missing_observation_uses_matching_return_intervals(self):
        dates = pd.bdate_range("2025-01-01", periods=4)
        reference = pd.Series([100., 110., 121., 133.1], index=dates)
        target = pd.Series([50., 60.5, 66.55], index=dates[[0, 2, 3]])
        actual = aligned_returns(target, reference)
        np.testing.assert_allclose(actual.Target, actual.Reference, atol=1e-12)
        self.assertEqual(list(actual.index), list(dates[[2, 3]]))
        self.assertAlmostEqual(actual.Target.iloc[0], .21)

    def test_training_fit_is_unaffected_by_later_regime_change(self):
        dates = pd.bdate_range("2025-01-01", periods=300)
        x = np.sin(np.arange(300)) * .01
        original = pd.DataFrame({"Reference": x, "Target": .001 + 1.5 * x}, index=dates)
        changed = original.copy()
        changed.loc[dates[126]:, "Target"] = -3 * x[126:]
        a, b = proxy_diagnostics(original), proxy_diagnostics(changed)
        self.assertAlmostEqual(a["TrainingBeta"], 1.5)
        self.assertEqual(a["TrainingBeta"], b["TrainingBeta"])
        self.assertEqual(a["TrainingAlphaDaily"], b["TrainingAlphaDaily"])
        self.assertAlmostEqual(a["LaterR2FromFrozenFit"], 1.)
        self.assertLess(b["LaterR2FromFrozenFit"], 0.)

    def test_nonfinite_and_duplicate_price_observations_are_rejected(self):
        dates = pd.bdate_range("2025-01-01", periods=3)
        frame = pd.DataFrame({"Open": [1., 2., 3.], "Close": [1., 2., 3.]}, index=dates)
        broken = frame.copy()
        broken.loc[dates[1], "Close"] = np.inf
        with self.assertRaises(ValueError):
            validate_prices(broken, "nonfinite")
        with self.assertRaises(ValueError):
            validate_prices(pd.concat([frame, frame.iloc[-1:]]), "duplicate")


if __name__ == "__main__":
    unittest.main()
