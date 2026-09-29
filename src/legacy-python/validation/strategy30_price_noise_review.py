"""Research-only episode attribution and two small noise-filter tests for PRICE70."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import RESULT_DIR  # noqa: E402
from downloader import _load_fred_series, _load_hy_oas_observations  # noqa: E402
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT  # noqa: E402
from strategy30_31_yaml_parity import add_credit_observation  # noqa: E402
from strategy30_hy_oas_review import prepare, run_one  # noqa: E402
from strategy30_signal_combination_review import definition_for, event_metrics  # noqa: E402
from strategy30_unknown_crash_stress import (  # noqa: E402
    EVENT_END, EVENT_START, SCENARIOS, inject,
)

VARIANTS = ("BASE30", "PRICE70", "PERSIST2", "QUICK_RELEASE", "PERSIST2_QUICK")


def candidate_definition(variant: str) -> dict:
    if variant not in VARIANTS:
        raise ValueError(variant)
    if variant in ("BASE30", "PRICE70"):
        return definition_for(variant)
    definition = definition_for("PRICE70")
    definition["strategy"]["id"] += f"-{variant.lower()}"
    rules = definition["state"]["shock_guard"]["rules"]
    if variant in ("PERSIST2", "PERSIST2_QUICK"):
        rules[0]["confirm"] = 2
    if variant in ("QUICK_RELEASE", "PERSIST2_QUICK"):
        rules[1]["confirm"] = 1
    return definition


def shock_active(history: pd.DataFrame) -> pd.Series:
    return history.NotificationContext.map(
        lambda context: context["state_values"].get("shock_guard") == "TRUE"
    ).astype(bool)


def shock_episodes(history: pd.DataFrame, baseline: pd.DataFrame,
                   qqq: pd.Series, *, start: str, end: str,
                   event_start: str | None = None) -> list[dict]:
    """Decompose paired portfolio log returns during each shock-guard episode."""
    shock = shock_active(history).loc[start:end]
    dates = shock.index
    differences = (np.log(history.Portfolio).diff() -
                   np.log(baseline.Portfolio).diff())
    episodes = []
    entry = None
    for i, date in enumerate(dates):
        if shock.iloc[i] and entry is None:
            entry = date
        if entry is None or (shock.iloc[i] and i < len(dates) - 1):
            continue
        release = date if not shock.iloc[i] else dates[-1]
        price = qqq.loc[entry:release]
        trough = price.idxmin()
        prior = baseline.loc[:entry, "Portfolio"].iloc[-21:]
        before = differences.loc[entry:trough].sum()
        after = differences.loc[trough:release].sum() - differences.loc[trough]
        episodes.append({
            "Entry": str(entry.date()), "Release": str(release.date()),
            "QQQTrough": str(trough.date()),
            "QQQChangeToTrough": float(price.loc[trough] / price.loc[entry] - 1),
            "BaselineLossPrior20": float(
                baseline.loc[entry, "Portfolio"] / prior.iloc[0] - 1
            ),
            "BaselineLossAtEventEntry": (
                float(baseline.loc[entry, "Portfolio"] /
                      baseline.loc[event_start, "Portfolio"] - 1)
                if event_start is not None else None
            ),
            "Days": int(shock.loc[entry:release].sum()),
            "AdvantageToTrough": float(np.expm1(before)),
            "AdvantageAfterTrough": float(np.expm1(after)),
            "AdvantageTotal": float(np.expm1(before + after)),
        })
        entry = None
    return episodes


def evaluate(directory: Path, period: str, dates: tuple[str, str],
             proxy: bool, variants: tuple[str, ...],
             *, event: tuple[str, str] | None = None):
    outputs = {}
    for variant in variants:
        metrics, history, credit, deep = run_one(
            directory, candidate_definition(variant), dates, proxy=proxy, cost=1.0
        )
        outputs[variant] = (metrics, history, credit, deep)
        print({"Period": period, "Variant": variant, **metrics}, flush=True)
    qqq = pd.read_csv(directory / "QQQ.csv", index_col="Date", parse_dates=True)["Close"]
    baseline = outputs["BASE30"][1]
    rows, episodes = [], []
    start, end = event if event else dates
    for variant, (metrics, history, credit, deep) in outputs.items():
        shock = shock_active(history).loc[start:end]
        event_result = event_metrics(history, credit, deep, start, end)
        rows.append({"Period": period, "Variant": variant, **metrics,
                     "EventReturn": event_result["Return"],
                     "EventMDD": event_result["MDD"],
                     "ShockDaysEvent": int(shock.sum()),
                     "ShockEpisodesEvent": int((shock & ~shock.shift(1, fill_value=False)).sum())})
        if variant != "BASE30":
            episodes.extend({"Period": period, "Variant": variant, **episode}
                            for episode in shock_episodes(
                                history, baseline, qqq, start=start, end=end,
                                event_start=start if event else None,
                            ))
    return rows, episodes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", default=["CURRENT", "DOTCOM_PROXY", "GFC_PROXY"])
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--scenarios", nargs="+", default=[
        "FAST_SHARED_SILENT", "FAST_GROWTH_LATE_CREDIT", "FAST_V_RECOVERY"
    ])
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--output-stem", default="strategy30_price_noise")
    args = parser.parse_args()
    variants = tuple(dict.fromkeys(("BASE30", *args.variants)))
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    summary, episodes = [], []
    periods = (("CURRENT", CURRENT, False),
               ("DOTCOM_PROXY", DOTCOM, True),
               ("GFC_PROXY", GFC, True))
    for period, dates, proxy in periods:
        if period not in args.periods:
            continue
        with TemporaryDirectory(prefix="strategy30-price-noise-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare(directory, baa, hy, proxy=proxy, lag=1)
            rows, detail = evaluate(directory, period, dates, proxy, variants)
            summary.extend(rows)
            episodes.extend(detail)
    if args.stress:
        for name in args.scenarios:
            with TemporaryDirectory(prefix="strategy30-price-noise-stress-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                prepare(directory, baa, hy, proxy=False, lag=1)
                inject(directory, SCENARIOS[name])
                add_credit_observation(directory)
                rows, detail = evaluate(
                    directory, name, (CURRENT[0], str(EVENT_END.date())), False,
                    variants, event=(str(EVENT_START.date()), str(EVENT_END.date())),
                )
                summary.extend(rows)
                episodes.extend(detail)
    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summary).to_csv(RESULT_DIR / f"{args.output_stem}_summary.csv", index=False)
    pd.DataFrame(episodes).to_csv(RESULT_DIR / f"{args.output_stem}_episodes.csv", index=False)


if __name__ == "__main__":
    main()
