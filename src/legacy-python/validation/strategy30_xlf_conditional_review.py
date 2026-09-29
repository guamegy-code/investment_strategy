"""Research-only conditional XLF/SPY and BAA10Y signals on deployed Strategy 30.

Variants load the deployed YAML and change a copy in memory. Some change credit
entry; others add an independent financial guard and a 35% QQQ target.
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import RESULT_DIR  # noqa: E402
from downloader import _load_fred_series, _load_hy_oas_observations  # noqa: E402
from indicators import Indicator  # noqa: E402
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT  # noqa: E402
from strategy30_hy_oas_review import STRATEGY30, prepare, run_one  # noqa: E402
from strategy30_signal_combination_review import event_metrics  # noqa: E402
from strategy_dsl import load_strategy_definition  # noqa: E402

VARIANTS = ("BASE30", "FIN_LEAD", "FIN_PERSIST", "BAA_THEN_XLF", "BAA_AND_XLF",
            "FIN35", "FIN_PRELEAD", "FIN_PRELEAD_WINDOW")
ORIGINAL = ("state.relative_weak_age < 20 and BAA10Y.close >= 2 "
            "and variables.credit_change20 >= 0.30")
STRONG_BAA = "BAA10Y.close >= 2 and variables.credit_change20 >= 0.30"
MILD_BAA = "BAA10Y.close >= 1.8 and variables.credit_change20 >= 0.20"
FIN_WEAK = "XLF.relative_roc20 <= -8"
EVENTS = {
    "CURRENT": {"2018_Q4": ("2018-09-20", "2019-04-30"),
                "COVID": ("2020-02-19", "2020-08-31"),
                "2022_BEAR": ("2021-11-19", "2023-01-19"),
                "2023_BANKS": ("2023-03-01", "2023-06-30"),
                "2025": ("2025-02-19", "2025-04-08")},
    "DOTCOM_PROXY": {"DOTCOM": ("2000-03-27", "2002-10-07")},
    "GFC_PROXY": {"GFC": ("2007-10-31", "2009-03-09")},
}


def definition_for(variant: str) -> dict:
    if variant not in VARIANTS:
        raise ValueError(variant)
    definition = deepcopy(load_strategy_definition(STRATEGY30))
    if variant == "BASE30":
        return definition
    definition["strategy"]["id"] += f"-{variant.lower()}-research"
    definition["assets"]["observations"].append("XLF")
    if variant in ("FIN35", "FIN_PRELEAD", "FIN_PRELEAD_WINDOW"):
        trigger = (f"{FIN_WEAK} and {MILD_BAA}" if variant == "FIN35"
                   else "XLF.prelead_signal >= 1" if variant == "FIN_PRELEAD"
                   else "XLF.prelead_window_signal >= 1")
        definition["state"]["financial_guard"] = {
            "initial": "FALSE",
            "rules": [
                {"when": f"state.financial_guard == 'FALSE' and {trigger}",
                 "set": "TRUE"},
                {"when": "state.financial_guard == 'TRUE' "
                         "and XLF.relative_roc20 > -2 "
                         "and variables.credit_change20 <= 0 "
                         "and SPY.close > SPY.ema55",
                 "set": "FALSE", "confirm": 5},
            ],
        }
        position = next(i for i, rule in enumerate(definition["target"])
                        if rule.get("when") == "state.structural_bear_mode == 'TRUE'")
        definition["target"].insert(position, {
            "when": "state.financial_guard == 'TRUE'",
            "weights": {"QQQ": "35%", "BIL": "65%"},
        })
        return definition
    entry = definition["state"]["credit_guard"]["rules"][0]
    assert entry["when"].count(ORIGINAL) == 1
    if variant in ("FIN_LEAD", "FIN_PERSIST"):
        xlf = FIN_WEAK
        if variant == "FIN_PERSIST":
            xlf += " and XLF.weak_days10 >= 5"
        replacement = f"(({ORIGINAL}) or ({xlf} and {MILD_BAA}))"
    elif variant == "BAA_THEN_XLF":
        replacement = (
            f"({STRONG_BAA} and "
            f"(state.relative_weak_age < 20 or {FIN_WEAK}))"
        )
    else:
        replacement = f"(({ORIGINAL}) and {FIN_WEAK})"
    entry["when"] = entry["when"].replace(ORIGINAL, replacement)
    return definition


def prelead_window_signal(weak: pd.Series, baa_mild: pd.Series) -> pd.Series:
    """BAA onset after XLF weakness 5-10 completed QQQ sessions earlier."""
    prior_weak = weak.shift(5).rolling(6, min_periods=6).max().ge(1)
    onset = baa_mild & ~baa_mild.shift(1, fill_value=False)
    return (onset & prior_weak).astype(int)


def add_xlf(directory: Path, source: Path = ROOT / "tmp/XLF.csv", *,
            prelead_days: int = 5) -> None:
    if prelead_days < 1:
        raise ValueError("prelead_days must be positive")
    if not source.is_file():
        raise FileNotFoundError(f"Private adjusted XLF history required: {source}")
    qqq = pd.read_csv(directory / "QQQ.csv", index_col="Date", parse_dates=True)
    spy = pd.read_csv(directory / "SPY.csv", index_col="Date", parse_dates=True)
    xlf = pd.read_csv(source, index_col="Date", parse_dates=True).sort_index()
    if xlf.index.min() > pd.Timestamp("1999-01-01"):
        raise ValueError("XLF source does not cover the dotcom proxy")
    xlf = xlf.reindex(qqq.index, method="ffill")
    relative = xlf["Close"] / spy["Close"]
    xlf["RELATIVE_ROC20"] = relative.pct_change(20) * 100
    xlf["WEAK_DAYS10"] = xlf["RELATIVE_ROC20"].le(-8).rolling(10, min_periods=10).sum()
    weak = xlf["RELATIVE_ROC20"].le(-8)
    weak_streak = weak.groupby((~weak).cumsum()).cumsum()
    baa = pd.read_csv(directory / "BAA10Y.csv", index_col="Date", parse_dates=True)
    baa_mild = baa["Close"].ge(1.8) & baa["Close"].diff(20).ge(.20)
    xlf["PRELEAD_SIGNAL"] = (
        baa_mild & ~baa_mild.shift(1, fill_value=False) & weak_streak.ge(prelead_days)
    ).astype(int)
    # A financial warning 5-10 sessions before a *new* BAA mild warning
    # remains eligible even if XLF briefly recovers on the BAA date.
    xlf["PRELEAD_WINDOW_SIGNAL"] = prelead_window_signal(weak, baa_mild)
    xlf = Indicator.add_indicators(xlf.dropna(subset=["Close"]))
    xlf.index.name = "Date"
    xlf.to_csv(directory / "XLF.csv")


def xlf_signal_dates(directory: Path) -> pd.DataFrame:
    xlf = pd.read_csv(directory / "XLF.csv", index_col="Date", parse_dates=True)
    baa = pd.read_csv(directory / "BAA10Y.csv", index_col="Date", parse_dates=True)
    change20 = baa["Close"].diff(20)
    frame = pd.DataFrame(index=xlf.index)
    frame["XLFWeak"] = xlf["RELATIVE_ROC20"].le(-8)
    frame["XLFPersistent"] = frame.XLFWeak & xlf["WEAK_DAYS10"].ge(5)
    frame["BAAMild"] = baa["Close"].ge(1.8) & change20.ge(.20)
    frame["BAAStrong"] = baa["Close"].ge(2.0) & change20.ge(.30)
    frame["FINPrelead"] = xlf["PRELEAD_SIGNAL"].ge(1)
    frame["FINPreleadWindow"] = xlf["PRELEAD_WINDOW_SIGNAL"].ge(1)
    return frame


def first_date(signal: pd.Series, start: str, end: str) -> str:
    within = signal.loc[start:end]
    dates = within.index[within.fillna(False)]
    return str(dates[0].date()) if len(dates) else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+",
                        default=["CURRENT", "DOTCOM_PROXY", "GFC_PROXY"])
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--lag", type=int, default=1)
    parser.add_argument("--cost", type=float, default=1.0)
    parser.add_argument("--prelead-days", type=int, default=5)
    parser.add_argument("--output-stem", default="strategy30_xlf_conditional")
    args = parser.parse_args()
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    summaries, events, transitions, signal_dates = [], [], [], []
    periods = (("CURRENT", CURRENT, False),
               ("DOTCOM_PROXY", DOTCOM, True),
               ("GFC_PROXY", GFC, True))
    for period, dates, proxy in periods:
        if period not in args.periods:
            continue
        with TemporaryDirectory(prefix="strategy30-xlf-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare(directory, baa, hy, proxy=proxy, lag=args.lag)
            add_xlf(directory, prelead_days=args.prelead_days)
            signals = xlf_signal_dates(directory)
            for event, (start, end) in EVENTS[period].items():
                signal_dates.append({
                    "Period": period, "Event": event,
                    "XLFWeakFirst": first_date(signals.XLFWeak, start, end),
                    "FINLeadFirst": first_date(signals.XLFWeak & signals.BAAMild,
                                               start, end),
                    "FINPersistFirst": first_date(signals.XLFPersistent & signals.BAAMild,
                                                  start, end),
                    "FINPreleadFirst": first_date(signals.FINPrelead, start, end),
                    "FINPreleadWindowFirst": first_date(signals.FINPreleadWindow,
                                                        start, end),
                    "BAAMildFirst": first_date(signals.BAAMild, start, end),
                    "BAAStrongFirst": first_date(signals.BAAStrong, start, end),
                })
            for variant in args.variants:
                metrics, history, credit, deep = run_one(
                    directory, definition_for(variant), dates, proxy=proxy,
                    cost=args.cost,
                )
                finance = history.NotificationContext.map(
                    lambda context: context["state_values"].get("financial_guard") == "TRUE"
                ).astype(bool)
                summaries.append({"Period": period, "Variant": variant,
                                  "LagSessions": args.lag, "CostMultiple": args.cost,
                                  "PreleadDays": args.prelead_days,
                                  "FinanceEpisodes": int((finance & ~finance.shift(
                                      1, fill_value=False)).sum()),
                                  "FinanceDays": int(finance.sum()),
                                  **metrics})
                for event, (start, end) in EVENTS[period].items():
                    event_finance = finance.loc[start:end]
                    first_finance = event_finance.index[
                        event_finance & ~event_finance.shift(1, fill_value=False)
                    ]
                    events.append({"Period": period, "Variant": variant,
                                   "Event": event,
                                   "FirstFinance": (str(first_finance[0].date())
                                                    if len(first_finance) else ""),
                                   "FinanceDays": int(event_finance.sum()),
                                   **event_metrics(history, credit, deep, start, end)})
                changed = (credit.ne(credit.shift(1, fill_value=False)) |
                           finance.ne(finance.shift(1, fill_value=False)))
                for date in history.index[changed]:
                    transitions.append({"Period": period, "Variant": variant,
                                        "Date": str(date.date()),
                                        "CreditGuard": bool(credit.loc[date]),
                                        "FinanceGuard": bool(finance.loc[date])})
                print(summaries[-1], flush=True)
    RESULT_DIR.mkdir(exist_ok=True)
    for suffix, rows in (("summary", summaries), ("events", events),
                         ("transitions", transitions), ("signals", signal_dates)):
        if rows:
            pd.DataFrame(rows).to_csv(RESULT_DIR / f"{args.output_stem}_{suffix}.csv",
                                      index=False)


if __name__ == "__main__":
    main()
