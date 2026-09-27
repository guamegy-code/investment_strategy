"""Compare recovery gates for the strategy-28 relative-strength/credit overlay."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_failed_dip_regime import (
    CURRENT, DOTCOM, GFC, EVENTS, ROOT, RESULT_DIR,
    build_data, event_result, run_one, transitions,
)


def main() -> None:
    rows, event_rows, transition_rows = [], [], []
    modes = (
        "strict", "improving20", "peak60", "cycle_peak",
        "cycle_peak_spy", "trend_only",
    )
    periods = (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    )
    for period, dates, proxy in periods:
        with TemporaryDirectory(prefix="credit-release-", dir=ROOT / "tmp") as temp:
            data_dir = Path(temp)
            build_data(data_dir, dotcom=proxy)
            configs = (("BASE_28", -1e9, "strict", False),) + tuple(
                (mode, -5.0, mode, True) for mode in modes
            )
            for label, threshold, mode, credit_gate in configs:
                summary, history = run_one(
                    data_dir, *dates, threshold, True, credit_gate,
                    cape_proxy=proxy, release_mode=mode,
                )
                rows.append({"Period": period, "Mode": label, **summary})
                transition_rows.extend(transitions(period, label, history))
                events = (
                    {name: span for name, span in EVENTS.items() if span[0] >= CURRENT[0]}
                    if period == "CURRENT" else
                    {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                    {"GFC": ("2007-10-31", "2009-03-09")}
                )
                for event, span in events.items():
                    event_rows.append({
                        "Period": period, "Mode": label, "Event": event,
                        **event_result(history, *span),
                    })
                print(rows[-1], flush=True)

    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULT_DIR / "strategy28_credit_release_summary.csv", index=False)
    pd.DataFrame(event_rows).to_csv(RESULT_DIR / "strategy28_credit_release_events.csv", index=False)
    pd.DataFrame(transition_rows).to_csv(
        RESULT_DIR / "strategy28_credit_release_transitions.csv", index=False
    )


if __name__ == "__main__":
    main()
