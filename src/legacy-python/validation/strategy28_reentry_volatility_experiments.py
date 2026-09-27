"""Test two structural crash-response overlays for strategy 28.

1. Re-entry memory: if a defensive re-entry breaks the defense-period low,
   cap the next risk exposure until the failed rebound high is recovered.
2. Volatility budget: cap QQQ exposure when 20/60-day realized volatility
   rises, with immediate tightening and a five-day hysteretic release.

The dot-com replay uses lagged S&P 500 Shiller CAPE in place of the production
valuation score and an ^IRX-based pre-inception BIL proxy.  Its results are
reported separately from the production-data replay.
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
WINDOWS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "COVID": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2021-11-19", "2023-01-19"),
    "2025": ("2025-02-19", "2025-04-08"),
    "DOTCOM_CRASH": ("2000-03-27", "2002-10-07"),
    "DOTCOM_RECOVERY": ("2000-03-27", "2005-12-30"),
}


class CrashResponseOverlay:
    """Apply a bounded cap after the base strategy has selected its target."""

    STRATEGY_VERSION = "research-1"

    def __init__(
        self, *, memory_cap: float | None, volatility_budget: float | None,
        memory_secular_only: bool = False, cape_proxy: bool = False,
    ):
        definition = load_strategy_definition(STRATEGY)
        if cape_proxy:
            # Raw CAPE is on a different scale from the 0-100 composite score.
            definition["parameters"]["valuation_arm_score"] = 30.0
            definition["parameters"]["valuation_breakdown_points"] = 4.0
        self.base = DeclarativeStrategy(definition)
        self.memory_cap = memory_cap
        self.memory_secular_only = memory_secular_only
        self.volatility_budget = volatility_budget
        self.strategy_id = (
            f"strategy28-memory-{memory_cap}-vol-{volatility_budget}-research"
        )
        self.target = None
        self.previous_base_q = None
        self.defense_low = None
        self.probation = False
        self.probation_days = 0
        self.probation_support = None
        self.rebound_high = None
        self.memory_active = False
        self.memory_release_days = 0
        self.memory_failures = 0
        self.vol_cap = 1.0
        self.vol_release_days = 0
        self.vol_tightenings = 0
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
        qqq.update(("VOL20", "VOL60"))
        fields["QQQ"] = tuple(sorted(qqq))
        return fields

    def _update_memory(self, price: float, base_q: float, market: Mapping[str, Any]) -> None:
        if self.memory_cap is None:
            return
        if base_q < 0.95 and not self.probation and not self.memory_active:
            self.defense_low = price if self.defense_low is None else min(self.defense_low, price)

        if (
            self.previous_base_q is not None
            and base_q - self.previous_base_q >= 0.15
            and self.defense_low is not None
            and not self.memory_active
        ):
            self.probation = True
            self.probation_days = 0
            self.probation_support = self.defense_low
            self.rebound_high = price
            self.defense_low = None

        if self.probation:
            self.probation_days += 1
            self.rebound_high = max(float(self.rebound_high), price)
            if price < float(self.probation_support) * 0.995:
                qqq = market["QQQ"]
                secular = (
                    price < float(qqq["EMA200"])
                    and float(qqq["EMA200_SLOPE20"]) < 0
                )
                self.memory_active = not self.memory_secular_only or secular
                self.memory_failures += int(self.memory_active)
                self.probation = False
                self.memory_release_days = 0
            elif self.probation_days >= 40:
                self.probation = False
                self.probation_support = None
                self.rebound_high = None

        if self.memory_active:
            qqq = market["QQQ"]
            recovered = (
                price > float(self.rebound_high)
                and price > float(qqq["EMA55"])
                and float(qqq["ROC20"]) > 0
            )
            self.memory_release_days = self.memory_release_days + 1 if recovered else 0
            if self.memory_release_days >= 3:
                self.memory_active = False
                self.memory_release_days = 0
                self.probation_support = None
                self.rebound_high = None

    def _desired_vol_cap(self, market: Mapping[str, Any]) -> float:
        if self.volatility_budget is None:
            return 1.0
        vol = max(float(market["QQQ"]["VOL20"]), float(market["QQQ"]["VOL60"]))
        raw = max(0.55, min(1.0, self.volatility_budget / max(vol, 0.01)))
        if raw < 0.625:
            return 0.55
        if raw < 0.775:
            return 0.70
        if raw < 0.925:
            return 0.85
        return 1.0

    def _update_volatility(self, market: Mapping[str, Any]) -> None:
        desired = self._desired_vol_cap(market)
        if desired < self.vol_cap:
            self.vol_cap = desired
            self.vol_release_days = 0
            self.vol_tightenings += 1
            return
        if desired <= self.vol_cap:
            self.vol_release_days = 0
            return
        vol = max(float(market["QQQ"]["VOL20"]), float(market["QQQ"]["VOL60"]))
        release_ready = vol <= 0.90 * float(self.volatility_budget) / self.vol_cap
        self.vol_release_days = self.vol_release_days + 1 if release_ready else 0
        if self.vol_release_days >= 5:
            self.vol_cap = desired
            self.vol_release_days = 0

    @staticmethod
    def _weights(portfolio: Any, market: Mapping[str, Any]) -> dict[str, float]:
        prices = {ticker: float(market[ticker]["Close"]) for ticker in ("QQQ", "BIL")}
        return portfolio.weights(prices)

    def evaluate(self, date: Any, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        base_signal = self.base.evaluate(date, market, portfolio)
        base_target = dict(base_signal["target"])
        base_q = float(base_target["QQQ"])
        price = float(market["QQQ"]["Close"])
        old_cap = min(self.memory_cap if self.memory_active else 1.0, self.vol_cap)
        self._update_memory(price, base_q, market)
        self._update_volatility(market)
        cap = min(self.memory_cap if self.memory_active else 1.0, self.vol_cap)

        target = dict(base_target)
        if target["QQQ"] > cap:
            reduction = target["QQQ"] - cap
            target["QQQ"] = cap
            target["BIL"] += reduction

        current = self._weights(portfolio, market)
        deviation = max(
            abs(float(current.get(ticker, 0.0)) - float(weight))
            for ticker, weight in target.items()
        )
        qqq_under = float(current.get("QQQ", 0.0)) - float(target["QQQ"]) <= -0.04
        overlay_changed = abs(cap - old_cap) > 1e-12
        overlay_binding = target != base_target
        rebalance = (
            base_signal["rebalance"]
            if not overlay_binding
            else overlay_changed or deviation >= 0.075 or qqq_under
        )
        self.previous_base_q = base_q
        self.target = target
        self.notification_context = deepcopy(self.base.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["overlay"] = {
            "memory_active": self.memory_active,
            "memory_failures": self.memory_failures,
            "volatility_cap": self.vol_cap,
            "effective_cap": cap,
        }
        return {
            "rebalance": rebalance,
            "target": target,
            "days": base_signal["days"],
            "reason": "CRASH_RESPONSE_CAP" if overlay_changed else base_signal.get("reason"),
        }


def metrics(history: pd.DataFrame) -> dict[str, float]:
    perf = Performance(history)
    return {
        "CAGR": perf.cagr(),
        "MDD": perf.mdd(),
        "Volatility": perf.volatility(),
        "Sharpe": perf.sharpe_ratio(),
        "Calmar": perf.calmar_ratio(),
    }


def event_metrics(history: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    period = history.loc[start:end]
    value = period.Portfolio
    return {
        "Return": float(value.iloc[-1] / value.iloc[0] - 1),
        "MDD": float((value / value.cummax() - 1).min()),
    }


def overlay_transitions(period: str, label: str, history: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    previous = None
    for date, context in history.NotificationContext.items():
        overlay = context.get("overlay") if isinstance(context, dict) else None
        if not isinstance(overlay, dict):
            continue
        current = (
            overlay["memory_active"], overlay["memory_failures"],
            overlay["volatility_cap"], overlay["effective_cap"],
        )
        if current != previous:
            rows.append({
                "Period": period,
                "Label": label,
                "Date": str(date.date()),
                "MemoryActive": current[0],
                "MemoryFailures": current[1],
                "VolatilityCap": current[2],
                "EffectiveCap": current[3],
                "QQQTarget": context["target_weights"]["QQQ"],
            })
        previous = current
    return rows


def rolling_comparison(runs: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    baseline = runs["BASE_28"]
    for years in (3, 5):
        for start_year in range(2012, 2027 - years):
            start, end = f"{start_year}-01-01", f"{start_year + years - 1}-12-31"
            base = metrics(baseline.loc[start:end])
            for label, history in runs.items():
                if label == "BASE_28":
                    continue
                candidate = metrics(history.loc[start:end])
                rows.append({
                    "Years": years,
                    "Window": f"{start_year}_{start_year + years - 1}",
                    "Label": label,
                    "CAGRGap": candidate["CAGR"] - base["CAGR"],
                    "MDDImprovement": candidate["MDD"] - base["MDD"],
                    "CalmarGap": candidate["Calmar"] - base["Calmar"],
                })
    return pd.DataFrame(rows)


def run_one(
    label: str,
    memory_cap: float | None,
    volatility_budget: float | None,
    memory_secular_only: bool,
    data_dir: Path,
    start: str,
    end: str,
    *,
    cost_multiple: float = 1.0,
    delay: int = 0,
    cape_proxy: bool = False,
) -> tuple[dict[str, Any], pd.DataFrame, CrashResponseOverlay]:
    strategy = CrashResponseOverlay(
        memory_cap=memory_cap,
        volatility_budget=volatility_budget,
        memory_secular_only=memory_secular_only,
        cape_proxy=cape_proxy,
    )
    history, trades, rebalances = Backtest(
        strategy,
        data_dir=data_dir,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
        signal_delay_days=delay,
        start_date=start,
        end_date=end,
    ).run_all()
    return {
        "Label": label,
        "CostMultiple": cost_multiple,
        "Delay": delay,
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "MemoryFailures": strategy.memory_failures,
        "VolTightenings": strategy.vol_tightenings,
        **metrics(history),
    }, history, strategy


def build_dotcom_data(directory: Path) -> None:
    qqq = pd.read_csv(EXTENDED_DATA_DIR / "QQQ.csv", index_col="Date", parse_dates=True)
    cape = pd.read_csv(DATA_DIR / "shiller_cape.csv", parse_dates=["Date"])
    cape = cape.loc[cape.PE10.gt(0), ["Date", "PE10"]].set_index("Date").sort_index()
    cape.index = cape.index.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    qqq["VALUATION_SCORE"] = cape.PE10.reindex(qqq.index, method="ffill")
    qqq.to_csv(directory / "QQQ.csv", index_label="Date")
    for ticker, source in (("SPY", DATA_DIR), ("BIL", EXTENDED_DATA_DIR)):
        pd.read_csv(source / f"{ticker}.csv").to_csv(directory / f"{ticker}.csv", index=False)


def main() -> None:
    candidates = {
        "BASE_28": (None, None, False),
        "MEMORY_65": (0.65, None, False),
        "MEMORY_75": (0.75, None, False),
        "MEMORY_65_SECULAR": (0.65, None, True),
        "MEMORY_75_SECULAR": (0.75, None, True),
        "VOL_25": (None, 0.25, False),
        "VOL_22": (None, 0.22, False),
        "MEMORY65_VOL25": (0.65, 0.25, False),
    }
    summary_rows, event_rows, stress_rows, transition_rows = [], [], [], []

    current_runs = {}
    for label, (memory_cap, vol_budget, secular_only) in candidates.items():
        row, history, strategy = run_one(
            label, memory_cap, vol_budget, secular_only, DATA_DIR, *CURRENT
        )
        row["Period"] = "CURRENT"
        summary_rows.append(row)
        current_runs[label] = history
        transition_rows.extend(overlay_transitions("CURRENT", label, history))
        for event, (start, end) in WINDOWS.items():
            if start >= CURRENT[0]:
                event_rows.append({"Period": "CURRENT", "Event": event, "Label": label, **event_metrics(history, start, end)})
        print(row, flush=True)

    with TemporaryDirectory(prefix="strategy28-structural-", dir=ROOT / "tmp") as temp:
        dotcom_dir = Path(temp)
        build_dotcom_data(dotcom_dir)
        for label, (memory_cap, vol_budget, secular_only) in candidates.items():
            row, history, strategy = run_one(
                label, memory_cap, vol_budget, secular_only, dotcom_dir, *DOTCOM,
                cape_proxy=True,
            )
            row["Period"] = "DOTCOM_PROXY"
            summary_rows.append(row)
            transition_rows.extend(overlay_transitions("DOTCOM_PROXY", label, history))
            for event in ("DOTCOM_CRASH", "DOTCOM_RECOVERY"):
                start, end = WINDOWS[event]
                event_rows.append({"Period": "DOTCOM_PROXY", "Event": event, "Label": label, **event_metrics(history, start, end)})
            print(row, flush=True)

    for label in ("BASE_28", "MEMORY_75", "MEMORY_65_SECULAR", "MEMORY_75_SECULAR", "VOL_25"):
        memory_cap, vol_budget, secular_only = candidates[label]
        for cost, delay in ((3.0, 0), (1.0, 1), (1.0, 2)):
            row, _, _ = run_one(
                label, memory_cap, vol_budget, secular_only, DATA_DIR, *CURRENT,
                cost_multiple=cost, delay=delay,
            )
            stress_rows.append(row)

    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_summary.csv", index=False
    )
    pd.DataFrame(event_rows).to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_events.csv", index=False
    )
    pd.DataFrame(stress_rows).to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_stress.csv", index=False
    )
    rolling = rolling_comparison(current_runs)
    rolling.to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_rolling.csv", index=False
    )
    rolling.groupby(["Years", "Label"], as_index=False).agg(
        Windows=("Window", "count"),
        CAGRWins=("CAGRGap", lambda values: int((values > 0).sum())),
        AverageCAGRGap=("CAGRGap", "mean"),
        MDDWins=("MDDImprovement", lambda values: int((values > 0).sum())),
        AverageMDDImprovement=("MDDImprovement", "mean"),
        CalmarWins=("CalmarGap", lambda values: int((values > 0).sum())),
    ).to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_rolling_summary.csv", index=False
    )
    pd.DataFrame(transition_rows).to_csv(
        RESULT_DIR / "strategy28_reentry_volatility_transitions.csv", index=False
    )


if __name__ == "__main__":
    main()
