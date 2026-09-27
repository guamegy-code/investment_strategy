"""Test early QQQ defense using relative weakness and credit deterioration.

Price-only variants require a stage-2 new low. The credit variant can enter
earlier; release gates are varied independently in the companion review.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from typing import Any, Mapping

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from backtest import Backtest  # noqa: E402
from config import COMMISSION, DATA_DIR, EXTENDED_DATA_DIR, RESULT_DIR, SLIPPAGE  # noqa: E402
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
STRATEGY = ROOT / "strategies/28_qqq_valuation_warning_dip_buyer.yaml"
CURRENT = ("2012-01-03", "2026-07-31")
DOTCOM = ("2000-03-10", "2005-12-30")
GFC = ("2007-01-03", "2011-12-30")
EVENTS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "COVID": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2021-11-19", "2023-01-19"),
    "2025": ("2025-02-19", "2025-04-08"),
    "DOTCOM": ("2000-03-27", "2002-10-07"),
}


class FailedDipOverlay:
    STRATEGY_VERSION = "research-1"

    def __init__(
        self, relative_threshold: float | None, *, long_release: bool = False,
        credit_gate: bool = False, cape_proxy: bool = False,
        release_mode: str = "strict",
    ):
        definition = load_strategy_definition(STRATEGY)
        if cape_proxy:
            definition["parameters"]["valuation_arm_score"] = 30.0
            definition["parameters"]["valuation_breakdown_points"] = 4.0
        self.base = DeclarativeStrategy(definition)
        self.relative_threshold = relative_threshold
        self.long_release = long_release
        self.credit_gate = credit_gate
        self.release_mode = release_mode
        self.strategy_id = f"strategy28-failed-dip-{relative_threshold}-research"
        self.target = None
        self.previous_stage = 0
        self.cycle_low = None
        self.last_failure_low = None
        self.active = False
        self.release_days = 0
        self.credit_peak = None
        self.activations = 0
        self.notification_context = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    @property
    def required_market_fields(self) -> dict[str, tuple[str, ...]]:
        fields = {
            ticker: tuple(values)
            for ticker, values in self.base.required_market_fields.items()
        }
        qqq = set(fields.get("QQQ", ()))
        qqq.update((
            "RELATIVE_ROC20", "RELATIVE_ROC20_MIN20", "RELATIVE_ROC60",
            "BAA_SPREAD", "BAA_CHANGE20",
            "BAA_DROP60",
        ))
        fields["QQQ"] = tuple(sorted(qqq))
        return fields

    @staticmethod
    def _weights(portfolio: Any, market: Mapping[str, Any]) -> dict[str, float]:
        prices = {ticker: float(market[ticker]["Close"]) for ticker in ("QQQ", "BIL")}
        return portfolio.weights(prices)

    def _gate(self, relative_roc20: float, qqq: Mapping[str, Any]) -> bool:
        relative_ok = (
            self.relative_threshold is None
            or relative_roc20 <= self.relative_threshold
        )
        if not self.credit_gate:
            return relative_ok
        return (
            float(qqq["RELATIVE_ROC20_MIN20"]) <= float(self.relative_threshold)
            and float(qqq["BAA_SPREAD"]) >= 2.0
            and float(qqq["BAA_CHANGE20"]) >= 0.30
        )

    def evaluate(self, date: Any, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        base_signal = self.base.evaluate(date, market, portfolio)
        base_target = dict(base_signal["target"])
        context = self.base.notification_context
        stage = int(context["state_values"]["stage"])
        qqq = market["QQQ"]
        price = float(qqq["Close"])
        relative_roc20 = float(qqq["RELATIVE_ROC20"])
        relative_roc60 = float(qqq["RELATIVE_ROC60"])
        old_active = self.active

        if stage == 0 and not self.credit_gate:
            self.cycle_low = None
            self.last_failure_low = None
            self.active = False
            self.release_days = 0
            self.credit_peak = None
        else:
            new_low = self.cycle_low is None or price < self.cycle_low * 0.995
            if new_low:
                self.cycle_low = price
            rearm_low = (
                self.last_failure_low is None
                or price < self.last_failure_low * 0.995
            )
            # The relative-strength + credit pair is itself the regime warning.
            # Do not wait for the base strategy's drawdown stage, which can lag a
            # slow sector-led bear market such as 2022.
            minimum_stage = 0 if self.credit_gate else 2
            # Credit deterioration can become confirmed between price lows.  Requiring
            # the confirmation and a fresh low on the same day delays (or misses) the
            # intended regime signal, so only the price-only variants require new_low.
            entry_timing = True if self.credit_gate else new_low
            if (
                not self.active and stage >= minimum_stage and entry_timing and rearm_low
                and self._gate(relative_roc20, qqq)
            ):
                self.active = True
                self.activations += 1
                self.last_failure_low = price
                self.release_days = 0
                self.credit_peak = float(qqq["BAA_SPREAD"])

        if self.active:
            self.credit_peak = max(
                self.credit_peak or float(qqq["BAA_SPREAD"]),
                float(qqq["BAA_SPREAD"]),
            )
            recovered = (
                price > float(qqq["EMA200"])
                and float(qqq["EMA55"]) > float(qqq["EMA200"])
                and relative_roc60 > 0
            ) if self.long_release else (
                price > float(qqq["EMA55"])
                and float(qqq["EMA20"]) > float(qqq["EMA55"])
                and relative_roc20 > 0
            )
            if self.credit_gate:
                spread = float(qqq["BAA_SPREAD"])
                if self.release_mode == "strict":
                    recovered = recovered and spread < 2.0
                elif self.release_mode == "improving20":
                    recovered = recovered and float(qqq["BAA_CHANGE20"]) <= -0.30
                elif self.release_mode == "peak60":
                    recovered = recovered and float(qqq["BAA_DROP60"]) >= 0.50
                elif self.release_mode == "cycle_peak":
                    recovered = recovered and (
                        spread < 2.0 or self.credit_peak - spread >= 0.50
                    )
                elif self.release_mode == "cycle_peak_spy":
                    spy = market["SPY"]
                    recovered = (
                        recovered
                        and (spread < 2.0 or self.credit_peak - spread >= 0.50)
                        and float(spy["Close"]) > float(spy["EMA200"])
                    )
                elif self.release_mode == "trend_spy":
                    spy = market["SPY"]
                    recovered = (
                        recovered
                        and float(spy["Close"]) > float(spy["EMA200"])
                    )
                elif self.release_mode != "trend_only":
                    raise ValueError(f"Unknown release mode: {self.release_mode}")
            self.release_days = self.release_days + 1 if recovered else 0
            if self.release_days >= (10 if self.long_release else 5):
                self.active = False
                self.release_days = 0
                if self.credit_gate:
                    self.last_failure_low = None

        target = dict(base_target)
        if self.active and target["QQQ"] > 0.65:
            reduction = target["QQQ"] - 0.65
            target["QQQ"] = 0.65
            target["BIL"] += reduction

        current = self._weights(portfolio, market)
        deviation = max(
            abs(float(current.get(ticker, 0.0)) - float(weight))
            for ticker, weight in target.items()
        )
        qqq_under = float(current.get("QQQ", 0.0)) - float(target["QQQ"]) <= -0.04
        binding = target != base_target
        rebalance = (
            base_signal["rebalance"] or old_active != self.active
            if not binding
            else old_active != self.active or deviation >= 0.075 or qqq_under
        )
        self.previous_stage = stage
        self.target = target
        self.notification_context = deepcopy(context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["failed_dip"] = {
            "active": self.active,
            "activations": self.activations,
            "relative_roc20": relative_roc20,
            "relative_roc60": relative_roc60,
            "baa_spread": float(qqq["BAA_SPREAD"]),
            "baa_change20": float(qqq["BAA_CHANGE20"]),
        }
        return {
            "rebalance": rebalance,
            "target": target,
            "days": base_signal["days"],
            "reason": "FAILED_DIP_CAP" if old_active != self.active else base_signal.get("reason"),
        }


def build_data(directory: Path, *, dotcom: bool) -> None:
    qqq_source = EXTENDED_DATA_DIR if dotcom else DATA_DIR
    qqq = pd.read_csv(qqq_source / "QQQ.csv", index_col="Date", parse_dates=True)
    spy = pd.read_csv(DATA_DIR / "SPY.csv", index_col="Date", parse_dates=True)
    aligned = pd.concat(
        [qqq.Close.rename("QQQ"), spy.Close.rename("SPY")], axis=1, sort=False
    ).dropna()
    relative = aligned.QQQ / aligned.SPY
    qqq["RELATIVE_ROC20"] = (relative.pct_change(20) * 100).reindex(qqq.index)
    qqq["RELATIVE_ROC20_MIN20"] = qqq["RELATIVE_ROC20"].rolling(20).min()
    qqq["RELATIVE_ROC60"] = (relative.pct_change(60) * 100).reindex(qqq.index)
    credit_path = ROOT / "tmp" / "BAA10Y.csv"
    if not credit_path.is_file():
        raise RuntimeError(
            "Download FRED BAA10Y.csv to tmp/BAA10Y.csv before running this research"
        )
    credit = pd.read_csv(credit_path, parse_dates=["observation_date"])
    credit = pd.to_numeric(
        credit.set_index("observation_date").iloc[:, 0], errors="coerce"
    ).sort_index().ffill()
    credit = credit.reindex(qqq.index, method="ffill")
    qqq["BAA_SPREAD"] = credit
    qqq["BAA_CHANGE20"] = credit - credit.shift(20)
    qqq["BAA_DROP60"] = credit.rolling(60).max() - credit
    if dotcom:
        cape = pd.read_csv(DATA_DIR / "shiller_cape.csv", parse_dates=["Date"])
        cape = cape.loc[cape.PE10.gt(0), ["Date", "PE10"]].set_index("Date").sort_index()
        cape.index = cape.index.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
        qqq["VALUATION_SCORE"] = cape.PE10.reindex(qqq.index, method="ffill")
    qqq.to_csv(directory / "QQQ.csv", index_label="Date")
    spy.to_csv(directory / "SPY.csv", index_label="Date")
    bil_source = EXTENDED_DATA_DIR if dotcom else DATA_DIR
    pd.read_csv(bil_source / "BIL.csv").to_csv(directory / "BIL.csv", index=False)


def summarize(history: pd.DataFrame, trades: pd.DataFrame, rebalances: list, strategy: FailedDipOverlay) -> dict[str, Any]:
    perf = Performance(history)
    return {
        "CAGR": perf.cagr(), "MDD": perf.mdd(),
        "Sharpe": perf.sharpe_ratio(), "Calmar": perf.calmar_ratio(),
        "Trades": len(trades), "Rebalances": len(rebalances),
        "Activations": strategy.activations,
    }


def run_one(
    data_dir: Path, start: str, end: str, threshold: float | None, long_release: bool,
    credit_gate: bool,
    *, cape_proxy: bool = False, cost_multiple: float = 1.0,
    release_mode: str = "strict",
) -> tuple[dict[str, Any], pd.DataFrame]:
    strategy = FailedDipOverlay(
        threshold, long_release=long_release, credit_gate=credit_gate,
        cape_proxy=cape_proxy, release_mode=release_mode,
    )
    history, trades, rebalances = Backtest(
        strategy, data_dir=data_dir, tickers=strategy.required_tickers,
        start_date=start, end_date=end,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
    ).run_all()
    return summarize(history, trades, rebalances, strategy), history


def event_result(history: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    value = history.loc[start:end, "Portfolio"]
    return {
        "Return": float(value.iloc[-1] / value.iloc[0] - 1),
        "MDD": float((value / value.cummax() - 1).min()),
    }


def transitions(period: str, label: str, history: pd.DataFrame) -> list[dict[str, Any]]:
    rows, previous = [], None
    for date, context in history.NotificationContext.items():
        state = context["failed_dip"]
        current = (state["active"], state["activations"])
        if current != previous:
            rows.append({
                "Period": period, "Label": label, "Date": str(date.date()),
                "Active": current[0], "Activations": current[1],
                "RelativeROC20": state["relative_roc20"],
                "RelativeROC60": state["relative_roc60"],
                "BAASpread": state["baa_spread"],
                "BAAChange20": state["baa_change20"],
                "Stage": context["state_values"]["stage"],
                "QQQTarget": context["target_weights"]["QQQ"],
            })
        previous = current
    return rows


def main() -> None:
    profiles = {
        "BASE_28": (-1e9, False, False),
        "FAILED_DIP_ALL": (None, False, False),
        "FAILED_DIP_REL10_FAST": (-10.0, False, False),
        "FAILED_DIP_REL10_LONG": (-10.0, True, False),
        "FAILED_DIP_REL5_LONG": (-5.0, True, False),
        "FAILED_DIP_CREDIT_REL5": (-5.0, True, True),
    }
    summary_rows, event_rows, transition_rows, histories = [], [], [], {}
    with TemporaryDirectory(prefix="failed-dip-current-", dir=ROOT / "tmp") as temp:
        current_dir = Path(temp)
        build_data(current_dir, dotcom=False)
        for label, (threshold, long_release, credit_gate) in profiles.items():
            row, history = run_one(
                current_dir, *CURRENT, threshold, long_release, credit_gate
            )
            row.update({"Period": "CURRENT", "Label": label})
            summary_rows.append(row)
            histories[label] = history
            transition_rows.extend(transitions("CURRENT", label, history))
            for event, (start, end) in EVENTS.items():
                if start >= CURRENT[0]:
                    event_rows.append({"Period": "CURRENT", "Label": label, "Event": event, **event_result(history, start, end)})
            print(row, flush=True)

        for label in (
            "BASE_28", "FAILED_DIP_REL10_LONG", "FAILED_DIP_REL5_LONG",
            "FAILED_DIP_CREDIT_REL5",
        ):
            threshold, long_release, credit_gate = profiles[label]
            row, _ = run_one(
                current_dir, *CURRENT, threshold, long_release, credit_gate,
                cost_multiple=3.0,
            )
            row.update({"Period": "CURRENT_COST3", "Label": label})
            summary_rows.append(row)

    with TemporaryDirectory(prefix="failed-dip-dotcom-", dir=ROOT / "tmp") as temp:
        dotcom_dir = Path(temp)
        build_data(dotcom_dir, dotcom=True)
        for label, (threshold, long_release, credit_gate) in profiles.items():
            row, history = run_one(
                dotcom_dir, *DOTCOM, threshold, long_release, credit_gate,
                cape_proxy=True,
            )
            row.update({"Period": "DOTCOM_PROXY", "Label": label})
            summary_rows.append(row)
            transition_rows.extend(transitions("DOTCOM_PROXY", label, history))
            event_rows.append({"Period": "DOTCOM_PROXY", "Label": label, "Event": "DOTCOM", **event_result(history, *EVENTS["DOTCOM"])})
            print(row, flush=True)

        for label in ("BASE_28", "FAILED_DIP_CREDIT_REL5"):
            threshold, long_release, credit_gate = profiles[label]
            row, history = run_one(
                dotcom_dir, *GFC, threshold, long_release, credit_gate,
                cape_proxy=True,
            )
            row.update({"Period": "GFC_PROXY", "Label": label})
            summary_rows.append(row)
            transition_rows.extend(transitions("GFC_PROXY", label, history))
            event_rows.append({
                "Period": "GFC_PROXY", "Label": label, "Event": "GFC",
                **event_result(history, "2007-10-31", "2009-03-09"),
            })

    rolling_rows = []
    baseline = histories["BASE_28"]
    for years in (3, 5):
        for start_year in range(2012, 2027 - years):
            start, end = f"{start_year}-01-01", f"{start_year + years - 1}-12-31"
            base = Performance(baseline.loc[start:end])
            for label in (
                "FAILED_DIP_ALL", "FAILED_DIP_REL10_FAST",
                "FAILED_DIP_REL10_LONG", "FAILED_DIP_REL5_LONG",
                "FAILED_DIP_CREDIT_REL5",
            ):
                candidate = Performance(histories[label].loc[start:end])
                rolling_rows.append({
                    "Years": years, "Window": f"{start_year}_{start_year + years - 1}",
                    "Label": label,
                    "CAGRGap": candidate.cagr() - base.cagr(),
                    "MDDImprovement": candidate.mdd() - base.mdd(),
                    "CalmarGap": candidate.calmar_ratio() - base.calmar_ratio(),
                })

    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(RESULT_DIR / "strategy28_failed_dip_summary.csv", index=False)
    pd.DataFrame(event_rows).to_csv(RESULT_DIR / "strategy28_failed_dip_events.csv", index=False)
    pd.DataFrame(transition_rows).to_csv(RESULT_DIR / "strategy28_failed_dip_transitions.csv", index=False)
    rolling = pd.DataFrame(rolling_rows)
    rolling.to_csv(RESULT_DIR / "strategy28_failed_dip_rolling.csv", index=False)
    rolling.groupby(["Years", "Label"], as_index=False).agg(
        Windows=("Window", "count"),
        CAGRWins=("CAGRGap", lambda values: int((values > 0).sum())),
        AverageCAGRGap=("CAGRGap", "mean"),
        MDDWins=("MDDImprovement", lambda values: int((values > 0).sum())),
        AverageMDDImprovement=("MDDImprovement", "mean"),
        CalmarWins=("CalmarGap", lambda values: int((values > 0).sum())),
    ).to_csv(RESULT_DIR / "strategy28_failed_dip_rolling_summary.csv", index=False)


if __name__ == "__main__":
    main()
