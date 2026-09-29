"""Research-only BAA10Y versus HY OAS review. Never edits strategy YAML.

Requires a private, pre-2026 FRED archive at tmp/BAMLH0A0HYM2_historical.csv.
The current FRED tail is merged at run time; no ICE observations are exported.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest
from config import COMMISSION, SLIPPAGE
from downloader import (
    _align_macro_to_qqq_sessions, _load_fred_series, _load_hy_oas_observations,
)
from performance import Performance
from strategy28_failed_dip_regime import (
    CURRENT, DOTCOM, EVENTS, GFC, ROOT, RESULT_DIR, FailedDipOverlay,
    build_data, event_result, summarize,
)


class CreditComparison(FailedDipOverlay):
    """Keep the same price and recovery gates; vary only credit entry evidence."""

    def __init__(self, model: str, *, cape_proxy: bool, hy_level=4.0,
                 hy_change=0.5, hy_z=1.0):
        super().__init__(
            -5.0, long_release=True, credit_gate=model != "PRICE_ONLY",
            cape_proxy=cape_proxy, release_mode="trend_spy",
        )
        self.model = model
        self.hy_level = hy_level
        self.hy_change = hy_change
        self.hy_z = hy_z
        self.strategy_id = f"strategy28-hy-oas-{model}-research"

    @property
    def required_market_fields(self):
        fields = super().required_market_fields
        qqq = set(fields["QQQ"])
        qqq.update(("HY_OAS_LEVEL", "HY_OAS_DIFF_5", "HY_OAS_DIFF_20",
                    "HY_OAS_MA20", "HY_OAS_Z252", "HY_OAS_DROP60"))
        fields["QQQ"] = tuple(sorted(qqq))
        return fields

    def _gate(self, relative_roc20, qqq):
        relative_ok = float(qqq["RELATIVE_ROC20_MIN20"]) <= -5.0
        baa = (float(qqq["BAA_SPREAD"]) >= 2.0
               and float(qqq["BAA_CHANGE20"]) >= 0.30)
        hy = (float(qqq["HY_OAS_LEVEL"]) >= self.hy_level
              and float(qqq["HY_OAS_DIFF_20"]) >= self.hy_change)
        if self.model == "PRICE_ONLY":
            return relative_ok
        if self.model == "BAA10Y":
            return relative_ok and baa
        if self.model == "HY_OAS":
            return relative_ok and hy
        if self.model == "BAA_AND_HY":
            return relative_ok and baa and hy
        if self.model == "HY_Z252":
            return relative_ok and float(qqq["HY_OAS_Z252"]) >= self.hy_z
        raise ValueError(self.model)


def attach_credit_features(directory: Path, baa: pd.Series, hy: pd.Series, lag: int) -> None:
    path = directory / "QQQ.csv"
    qqq = pd.read_csv(path, index_col="Date", parse_dates=True)
    # Derive changes after alignment and lag. Shifting before alignment would
    # mean a calendar day, not a QQQ session.
    b = _align_macro_to_qqq_sessions(baa, qqq.index, lag_sessions=lag)
    h = _align_macro_to_qqq_sessions(hy, qqq.index, lag_sessions=lag)
    qqq["BAA_SPREAD"] = b
    qqq["BAA_CHANGE20"] = b.diff(20)
    qqq["BAA_DROP60"] = b.rolling(60).max() - b
    qqq["HY_OAS_LEVEL"] = h
    qqq["HY_OAS_DIFF_5"] = h.diff(5)
    qqq["HY_OAS_DIFF_20"] = h.diff(20)
    qqq["HY_OAS_MA20"] = h.rolling(20).mean()
    std = h.rolling(252).std()
    qqq["HY_OAS_Z252"] = ((h - h.rolling(252).mean()) / std).where(std.gt(0))
    qqq["HY_OAS_DROP60"] = h.rolling(60).max() - h
    qqq.to_csv(path, index_label="Date")


def rolling_metrics(history: pd.DataFrame, years: int) -> list[dict]:
    rows = []
    portfolio = history["Portfolio"]
    for year in range(portfolio.index.min().year + years, portfolio.index.max().year + 1):
        end = pd.Timestamp(year, 12, 31)
        start = end - pd.DateOffset(years=years)
        window = history.loc[start:end]
        if len(window) < years * 200:
            continue
        perf = Performance(window)
        rows.append({"WindowYears": years, "Start": str(window.index.min().date()),
                     "End": str(window.index.max().date()),
                     "CAGR": perf.cagr(), "MDD": perf.mdd(),
                     "Sharpe": perf.sharpe_ratio(), "Calmar": perf.calmar_ratio()})
    return rows


def run_variant(directory, period, dates, proxy, model, lag, *, cost=1.0,
                hy_level=4.0, hy_change=0.5, hy_z=1.0):
    strategy = CreditComparison(model, cape_proxy=proxy, hy_level=hy_level,
                                hy_change=hy_change, hy_z=hy_z)
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost, slippage=SLIPPAGE * cost,
    ).run_all()
    return strategy, history, summarize(history, trades, rebalances, strategy)


def main() -> None:
    archive = ROOT / "tmp" / "BAMLH0A0HYM2_historical.csv"
    hy = _load_hy_oas_observations(archive)  # Fail before writing partial results.
    baa_path = ROOT / "tmp" / "BAA10Y.csv"
    if not baa_path.is_file():
        raise FileNotFoundError(f"BAA10Y research input missing: {baa_path}")
    baa = _load_fred_series(baa_path)
    summaries, events, changes, rollings = [], [], [], []
    periods = (("DOTCOM_PROXY", DOTCOM, True),
               ("GFC_PROXY", GFC, True), ("CURRENT", CURRENT, False))
    models = ("PRICE_ONLY", "BAA10Y", "HY_OAS", "BAA_AND_HY", "HY_Z252")
    for period, dates, proxy in periods:
        for lag in (0, 1, 2):
            with TemporaryDirectory(prefix="hy-oas-review-", dir=ROOT / "tmp") as temporary:
                directory = Path(temporary)
                build_data(directory, dotcom=proxy)
                attach_credit_features(directory, baa, hy, lag)
                configs = [(model, 1.0, 4.0, 0.5, 1.0) for model in models]
                if lag == 1:
                    configs.append(("HY_OAS", 3.0, 4.0, 0.5, 1.0))
                    configs.extend(
                        ("HY_OAS", 1.0, level, change, 1.0)
                        for level, change in ((3.75, 0.4), (4.25, 0.6))
                    )
                for model, cost, level, change, z in configs:
                    label = f"{model}_L{lag}_C{cost:g}_H{level:g}_D{change:g}"
                    strategy, history, metrics = run_variant(
                        directory, period, dates, proxy, model, lag,
                        cost=cost, hy_level=level, hy_change=change, hy_z=z,
                    )
                    summaries.append({"Period": period, "Variant": label, **metrics})
                    spans = ({k: v for k, v in EVENTS.items() if v[0] >= CURRENT[0]}
                             if period == "CURRENT" else
                             {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                             {"GFC": ("2007-10-31", "2009-03-09")})
                    for event, span in spans.items():
                        events.append({"Period": period, "Variant": label,
                                       "Event": event, **event_result(history, *span)})
                    previous = False
                    for date, context in history.NotificationContext.items():
                        state = context["failed_dip"]
                        active = state["active"]
                        if active != previous:
                            changes.append({"Period": period, "Variant": label,
                                            "Date": str(date.date()), "Active": active,
                                            "QQQTarget": context["target_weights"]["QQQ"]})
                        previous = active
                    if lag == 1 and cost == 1.0:
                        for years in (3, 5):
                            rollings.extend({"Period": period, "Variant": label, **row}
                                            for row in rolling_metrics(history, years))
                    print(summaries[-1], flush=True)
    RESULT_DIR.mkdir(exist_ok=True)
    for suffix, rows in (("summary", summaries), ("events", events),
                         ("transitions", changes), ("rolling", rollings)):
        pd.DataFrame(rows).to_csv(RESULT_DIR / f"strategy28_hy_oas_{suffix}.csv", index=False)


if __name__ == "__main__":
    main()
