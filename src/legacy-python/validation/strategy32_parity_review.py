"""Compare deployed Strategy 32 YAML with its research signal on identical data."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downloader import _load_fred_series, _load_hy_oas_observations
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT
from strategy30_hy_oas_review import prepare, run_one
from strategy30_xlf_conditional_review import add_xlf, definition_for
from strategy_dsl import load_strategy_definition


STRATEGY32 = ROOT / "strategies/32_qqq_valuation_financial_credit_guard_no_topup.yaml"


def run_parity(period: str, dates: tuple[str, str], *, proxy: bool) -> dict:
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    with TemporaryDirectory(prefix="strategy32-parity-", dir=ROOT / "tmp") as temp:
        directory = Path(temp)
        prepare(directory, baa, hy, proxy=proxy, lag=1)
        add_xlf(directory)
        expected, expected_history, _, _ = run_one(
            directory, definition_for("FIN_PRELEAD_WINDOW"), dates,
            proxy=proxy, cost=1.0,
        )
        actual, actual_history, _, _ = run_one(
            directory, load_strategy_definition(STRATEGY32), dates,
            proxy=proxy, cost=1.0,
        )
        if not expected_history.index.equals(actual_history.index):
            raise AssertionError(f"{period}: trading dates differ")
        for key in ("CAGR", "MDD", "Trades", "Rebalances"):
            if abs(expected[key] - actual[key]) > 1e-10:
                raise AssertionError(f"{period}: {key}: {expected[key]} != {actual[key]}")
        for date in actual_history.index:
            expected_context = expected_history.at[date, "NotificationContext"]
            actual_context = actual_history.at[date, "NotificationContext"]
            for key in ("financial_guard", "credit_guard", "deep_guard"):
                expected_value = expected_context["state_values"].get(key)
                actual_value = actual_context["state_values"].get(key)
                if expected_value != actual_value:
                    raise AssertionError(f"{period} {date.date()}: {key}: "
                                         f"{expected_value} != {actual_value}")
            if expected_context["target_weights"] != actual_context["target_weights"]:
                raise AssertionError(f"{period} {date.date()}: target differs")
        return {"Period": period, **actual}


if __name__ == "__main__":
    for name, dates, proxy in (("CURRENT", CURRENT, False),
                               ("DOTCOM_PROXY", DOTCOM, True),
                               ("GFC_PROXY", GFC, True)):
        print(run_parity(name, dates, proxy=proxy), flush=True)
