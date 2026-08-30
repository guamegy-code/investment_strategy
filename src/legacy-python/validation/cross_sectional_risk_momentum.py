"""Cross-sectional momentum inside Strategy 15's existing QQQ sleeve.

The Strategy 15 state/target schedule is frozen first.  The candidate changes
only the QQQ sleeve: each month it holds the top three assets by a lagged
12-minus-1-month KRW total-return signal.  There is no absolute-momentum gate
and no cash fallback, so this tests risk-engine selection rather than another
defensive overlay.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from backtest import Backtest
from config import COMMISSION, DATA_DIR, PROJECT_ROOT, RESULT_DIR, SLIPPAGE
from indicators import Indicator
from performance import Performance
from validation.state_conditioned_cross_asset_rotation import (
    Strategy15KrwSchedule,
    build_strategy15_schedule,
)


START_DATE = "2013-01-04"
SOURCE_DATA_DIR = PROJECT_ROOT / "data_cross_asset_rotation"
MULTIMARKET_DATA_DIR = PROJECT_ROOT / "data_multimarket"
RISK_DATA_DIR = PROJECT_ROOT / "data_risk_assets"
OUTPUT_DATA_DIR = PROJECT_ROOT / "data_cross_sectional_momentum"
LOCK_PATH = PROJECT_ROOT / "validation" / "cross_sectional_risk_momentum_lock.json"

QQQ = "QQQ"
TDF = "TDF2050_PROXY"
BIL = "BIL"
SIGNAL_FIELD = "XSMOM_12_1_LAG1"
CANDIDATES = (
    "QQQ",
    "SPY",
    "IWM",
    "VEA",
    "VWO",
    "KOSPI200",
    "VTV",
    "VIG",
    "USMV",
    "SPLV",
)
CORE = (QQQ, TDF, BIL)
ALL_HOLDINGS = tuple(dict.fromkeys((*CORE, *CANDIDATES)))
SELECTED_COUNT = 3
LOOKBACK_START = 252
SKIP_RECENT = 21
SIGNAL_LAG = 1
BASE_COST = COMMISSION + SLIPPAGE

WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    cost_multiple: float = 1.0
    execution_delay_days: int = 0


SCENARIOS = (
    Scenario("BASE"),
    Scenario("COST_3X", cost_multiple=3.0),
    Scenario("DELAY_1D", execution_delay_days=1),
)


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    if "Close" not in frame or frame.empty:
        raise ValueError("market frame has no usable Close")
    for column in ("Open", "High", "Low"):
        if column not in frame:
            frame[column] = frame["Close"]
    if "Volume" not in frame:
        frame["Volume"] = 0.0
    return frame.loc[:, ["Close", "High", "Low", "Open", "Volume"]]


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"required local market data is missing: {path}")
    return _normalise(pd.read_csv(path, index_col="Date", parse_dates=True))


def _convert_to_krw(frame: pd.DataFrame, fx: pd.Series) -> pd.DataFrame:
    converted = frame.copy()
    rate = fx.reindex(converted.index).ffill()
    converted = converted.loc[rate.notna()].copy()
    rate = rate.loc[converted.index]
    for column in ("Open", "High", "Low", "Close"):
        converted[column] = converted[column] * rate
    return converted


def build_cross_sectional_data(
    output_dir: Path = OUTPUT_DATA_DIR,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Build a common QQQ-calendar KRW data set from repository sources."""

    output_dir = Path(output_dir)
    expected = [output_dir / f"{ticker}.csv" for ticker in ALL_HOLDINGS]
    manifest_path = output_dir / "manifest.csv"
    if not refresh and manifest_path.exists() and all(path.exists() for path in expected):
        return pd.read_csv(manifest_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    fx = _read(DATA_DIR / "KRW=X.csv")["Close"]
    sources: dict[str, tuple[pd.DataFrame, str, bool]] = {
        "QQQ": (_read(SOURCE_DATA_DIR / "QQQ.csv"), "QQQ", False),
        "TDF2050_PROXY": (_read(SOURCE_DATA_DIR / "TDF2050_PROXY.csv"), "TDF2050_PROXY", False),
        "BIL": (_read(SOURCE_DATA_DIR / "BIL.csv"), "BIL", False),
        "VEA": (_read(SOURCE_DATA_DIR / "VEA.csv"), "VEA", False),
        "VWO": (_read(SOURCE_DATA_DIR / "VWO.csv"), "VWO", False),
        "KOSPI200": (_read(SOURCE_DATA_DIR / "KOSPI200.csv"), "069500.KS", False),
        "SPY": (_read(MULTIMARKET_DATA_DIR / "SPY.csv"), "SPY", True),
        "IWM": (_read(MULTIMARKET_DATA_DIR / "IWM.csv"), "IWM", True),
        "VTV": (_read(RISK_DATA_DIR / "VTV.csv"), "VTV", True),
        "VIG": (_read(RISK_DATA_DIR / "VIG.csv"), "VIG", True),
        "USMV": (_read(RISK_DATA_DIR / "USMV.csv"), "USMV", True),
        "SPLV": (_read(RISK_DATA_DIR / "SPLV.csv"), "SPLV", True),
    }
    prepared: dict[str, pd.DataFrame] = {}
    for ticker, (frame, _, needs_fx) in sources.items():
        prepared[ticker] = _convert_to_krw(frame, fx) if needs_fx else frame

    last_common = min(frame.index.max() for frame in prepared.values())
    first_common = max(frame.index.min() for frame in prepared.values())
    calendar = prepared[QQQ].index
    calendar = calendar[(calendar >= first_common) & (calendar <= last_common)]
    if len(calendar) < LOOKBACK_START + SIGNAL_LAG + 20:
        raise ValueError("insufficient common history for locked momentum signal")

    rows = []
    for ticker in ALL_HOLDINGS:
        source_frame, source_ticker, needs_fx = sources[ticker]
        frame = prepared[ticker].reindex(calendar).ffill()
        frame.index.name = "Date"
        frame = Indicator.add_indicators(frame)
        if ticker in CANDIDATES:
            raw_signal = (
                frame["Close"].shift(SKIP_RECENT)
                / frame["Close"].shift(LOOKBACK_START)
                - 1.0
            )
            frame[SIGNAL_FIELD] = raw_signal.shift(SIGNAL_LAG)
        frame.to_csv(output_dir / f"{ticker}.csv")
        rows.append({
            "Ticker": ticker,
            "SourceTicker": source_ticker,
            "CurrencyBasis": "KRW_LOCAL" if ticker == "KOSPI200" else "KRW_UNHEDGED",
            "ConvertedInBuilder": needs_fx,
            "StartDate": frame.index.min(),
            "EndDate": frame.index.max(),
            "Observations": len(frame),
        })
    manifest = pd.DataFrame(rows)
    manifest.to_csv(manifest_path, index=False)
    return manifest


class CrossSectionalRiskStrategy:
    """Replace the frozen QQQ target with a fixed or ranked equity mix."""

    def __init__(
        self,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        mode: str,
        record: Callable[[dict[str, Any]], None],
    ):
        if mode not in {"dynamic", "static"}:
            raise ValueError(f"unknown cross-sectional mode: {mode}")
        self.mode = mode
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self._record = record
        self.strategy_id = f"poc:cross-sectional-risk-{mode}"
        self.display_name = "XSMOM_TOP3" if mode == "dynamic" else "STATIC_EQUAL_UNIVERSE"
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = ALL_HOLDINGS
        self.observation_tickers = ()
        self.required_tickers = ALL_HOLDINGS
        self.required_market_fields = {
            ticker: (SIGNAL_FIELD,) for ticker in CANDIDATES
        } if mode == "dynamic" else {}
        self.risk_asset_tickers = CANDIDATES
        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.selected = tuple(CANDIDATES) if mode == "static" else ()
        self.last_review_month = None

    @staticmethod
    def _valid(value: Any) -> bool:
        try:
            return value is not None and isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def _rank(self, market: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, ...], dict[str, float]]:
        scores = {
            ticker: float(market[ticker][SIGNAL_FIELD])
            for ticker in CANDIDATES
            if self._valid(market[ticker].get(SIGNAL_FIELD))
        }
        if len(scores) != len(CANDIDATES):
            raise ValueError("locked cross-sectional universe is not fully signal-ready")
        selected = tuple(sorted(scores, key=lambda ticker: (-scores[ticker], ticker))[:SELECTED_COUNT])
        return selected, scores

    def _target(self, base_target: Mapping[str, float]) -> dict[str, float]:
        target = {ticker: 0.0 for ticker in ALL_HOLDINGS}
        risk_weight = float(base_target[QQQ])
        if self.selected:
            each = risk_weight / len(self.selected)
            for ticker in self.selected:
                target[ticker] = each
        target[TDF] = float(base_target[TDF])
        target[BIL] = float(base_target[BIL])
        total = sum(target.values())
        if not np.isclose(total, 1.0):
            target[BIL] += 1.0 - total
        if sum(target[ticker] for ticker in CANDIDATES) > 0.700000001:
            raise AssertionError("cross-sectional risk sleeve exceeds 70%")
        return {ticker: round(max(0.0, value), 10) for ticker, value in target.items()}

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        month = timestamp.to_period("M")
        changed = False
        scores: dict[str, float] = {}
        if self.mode == "dynamic" and month != self.last_review_month:
            previous = self.selected
            self.selected, scores = self._rank(market)
            self.last_review_month = month
            changed = self.selected != previous
            self._record({
                "Date": timestamp,
                "State": point["state"],
                "QQQSleeveWeight": float(point["target"][QQQ]),
                "Selected": ",".join(self.selected),
                "Scores": json.dumps(scores, sort_keys=True),
                "SelectionChanged": changed,
            })

        target = self._target(point["target"])
        risk_active = float(point["target"][QQQ]) > 1e-12
        baseline_rebalance = bool(point["rebalance"])
        rebalance = baseline_rebalance or (changed and risk_active)
        reason = point.get("reason") if baseline_rebalance else None
        if changed and risk_active:
            reason = f"{reason}|MONTHLY_XSMOM_TOP3" if reason else "MONTHLY_XSMOM_TOP3"

        self.state = point["state"]
        self.target = target
        self.risk_off_score = point.get("risk_off_score")
        self.recovery_score = point.get("recovery_score")
        self.safe_asset = BIL if target[BIL] > 0.0 else TDF
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": 1 if changed and risk_active else int(point["days"]),
            "reason": reason,
        }


def _run(strategy: Any, scenario: Scenario) -> dict[str, Any]:
    backtest = Backtest(
        strategy,
        data_dir=OUTPUT_DATA_DIR,
        tickers=strategy.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.execution_delay_days,
        start_date=START_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "label": strategy.display_name,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }


def _metrics(history: pd.DataFrame) -> dict[str, float]:
    values = Performance(history).summary()
    return {name: float(values[name]) for name in (
        "CAGR", "MDD", "Volatility", "Sharpe", "Sortino", "Calmar", "TransactionCosts"
    )}


def _window(history: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    selected = history.loc[start:end].copy()
    if len(selected) < 2:
        raise ValueError("cross-sectional performance window is empty")
    selected["Portfolio"] = selected["Portfolio"] / selected["Portfolio"].iloc[0]
    return selected


def _summary(results: list[dict[str, Any]], scenario: str) -> pd.DataFrame:
    rows = []
    for window_name, (start, end) in WINDOWS.items():
        common = None
        for result in results:
            index = result["history"].loc[start:end].index
            common = index if common is None else common.intersection(index)
        if common is None or len(common) < 2:
            raise ValueError("strategies have no common performance window")
        for result in results:
            history = result["history"].loc[common].copy()
            history["Portfolio"] = history["Portfolio"] / history["Portfolio"].iloc[0]
            rows.append({
                "Scenario": scenario,
                "Strategy": result["label"],
                "Window": window_name,
                "StartDate": history.index.min(),
                "EndDate": history.index.max(),
                **_metrics(history),
                "Rebalances": len(result["rebalances"]),
                "Trades": len(result["trades"]),
            })
    return pd.DataFrame(rows)


def _relative(summary: pd.DataFrame) -> pd.DataFrame:
    baseline = summary.loc[summary["Strategy"] == "STRATEGY15_QQQ"].set_index("Window")
    static = summary.loc[summary["Strategy"] == "STATIC_EQUAL_UNIVERSE"].set_index("Window")
    rows = []
    for _, candidate in summary.loc[summary["Strategy"] != "STRATEGY15_QQQ"].iterrows():
        window = candidate["Window"]
        row = {
            "Strategy": candidate["Strategy"],
            "Window": window,
            "CAGRGapVsStrategy15": candidate["CAGR"] - baseline.loc[window, "CAGR"],
            "MDDImprovementVsStrategy15": candidate["MDD"] - baseline.loc[window, "MDD"],
            "SharpeGapVsStrategy15": candidate["Sharpe"] - baseline.loc[window, "Sharpe"],
        }
        if candidate["Strategy"] == "XSMOM_TOP3":
            row["CAGRGapVsStaticEqual"] = candidate["CAGR"] - static.loc[window, "CAGR"]
        rows.append(row)
    return pd.DataFrame(rows)


def _rolling_gap(candidate: pd.DataFrame, baseline: pd.DataFrame, years: int) -> pd.DataFrame:
    common = candidate.index.intersection(baseline.index)
    endpoints = pd.Series(common, index=common).groupby(common.to_period("M")).last()
    rows = []
    for end in pd.DatetimeIndex(endpoints.to_numpy()):
        cutoff = end - pd.DateOffset(years=years)
        prior = common[common <= cutoff]
        if len(prior) == 0:
            continue
        start = prior[-1]
        elapsed = (end - start).days / 365.25
        if elapsed < years * 0.98:
            continue
        candidate_cagr = (candidate.loc[end, "Portfolio"] / candidate.loc[start, "Portfolio"]) ** (1 / elapsed) - 1
        baseline_cagr = (baseline.loc[end, "Portfolio"] / baseline.loc[start, "Portfolio"]) ** (1 / elapsed) - 1
        rows.append({"WindowYears": years, "StartDate": start, "EndDate": end, "CAGRGap": candidate_cagr - baseline_cagr})
    return pd.DataFrame(rows)


def _assert_lock() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    checks = {
        "start": lock["data"]["start_date"] == START_DATE,
        "candidates": lock["data"]["candidates"] == list(CANDIDATES),
        "lookback": lock["construction"]["lookback_start_days"] == LOOKBACK_START,
        "skip": lock["construction"]["skip_recent_days"] == SKIP_RECENT,
        "signal_lag": lock["construction"]["signal_lag_days"] == SIGNAL_LAG,
        "selected": lock["construction"]["selected_assets"] == SELECTED_COUNT,
        "cost": np.isclose(lock["construction"]["base_one_way_cost"], BASE_COST),
    }
    if not all(checks.values()):
        raise AssertionError(f"implementation differs from locked design: {checks}")
    return lock


def run_cross_sectional_risk_momentum_validation(*, refresh_data: bool = False) -> dict[str, pd.DataFrame]:
    lock = _assert_lock()
    universe = build_cross_sectional_data(refresh=refresh_data)
    schedule = build_strategy15_schedule()
    summary_frames = []
    relative_frames = []
    stress_rows = []
    base_results = None
    base_activity: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        activity: list[dict[str, Any]] = []
        required = ALL_HOLDINGS
        baseline = Strategy15KrwSchedule(schedule, required)
        baseline.display_name = "STRATEGY15_QQQ"
        static = CrossSectionalRiskStrategy(schedule, "static", lambda row: None)
        dynamic = CrossSectionalRiskStrategy(schedule, "dynamic", activity.append)
        results = [_run(baseline, scenario), _run(static, scenario), _run(dynamic, scenario)]
        report = _summary(results, scenario.name)
        relative = _relative(report)
        summary_frames.append(report)
        relative.insert(0, "Scenario", scenario.name)
        relative_frames.append(relative)
        full = relative.loc[(relative["Strategy"] == "XSMOM_TOP3") & (relative["Window"] == "FULL")].iloc[0]
        stress_rows.append({
            "Scenario": scenario.name,
            "CAGRGapVsStrategy15": full["CAGRGapVsStrategy15"],
            "MDDImprovementVsStrategy15": full["MDDImprovementVsStrategy15"],
            "CAGRGapVsStaticEqual": full["CAGRGapVsStaticEqual"],
        })
        if scenario.name == "BASE":
            base_results = results
            base_activity = activity

    if base_results is None:
        raise AssertionError("base scenario was not executed")
    summary = pd.concat(summary_frames, ignore_index=True)
    relative = pd.concat(relative_frames, ignore_index=True)
    stress = pd.DataFrame(stress_rows)
    activity = pd.DataFrame(base_activity)
    rolling = pd.concat([
        _rolling_gap(base_results[2]["history"], base_results[0]["history"], years)
        for years in (3, 5)
    ], ignore_index=True)

    selections = activity["Selected"].str.split(",").explode() if not activity.empty else pd.Series(dtype=str)
    counts = Counter(selections.dropna())
    selection_summary = pd.DataFrame([
        {
            "Asset": ticker,
            "SelectedMonths": counts.get(ticker, 0),
            "SelectionShare": counts.get(ticker, 0) / max(1, len(activity)),
        }
        for ticker in CANDIDATES
    ])

    base_relative = relative.loc[relative["Scenario"] == "BASE"].set_index(["Strategy", "Window"])
    stress_indexed = stress.set_index("Scenario")
    gate = lock["promotion_gates"]
    full = base_relative.loc[("XSMOM_TOP3", "FULL")]
    development = base_relative.loc[("XSMOM_TOP3", "DEVELOPMENT_PRE2021")]
    recent = base_relative.loc[("XSMOM_TOP3", "RECENT_2021_PRESENT")]
    share3 = float((rolling.loc[rolling["WindowYears"] == 3, "CAGRGap"] > 0.0).mean())
    share5 = float((rolling.loc[rolling["WindowYears"] == 5, "CAGRGap"] > 0.0).mean())
    distinct = int((selection_summary["SelectedMonths"] > 0).sum())
    maximum_share = float(selection_summary["SelectionShare"].max())
    checks = {
        "FullCAGR": full["CAGRGapVsStrategy15"] >= gate["full_cagr_gap_vs_strategy15_min"],
        "DevelopmentCAGR": development["CAGRGapVsStrategy15"] >= gate["development_cagr_gap_vs_strategy15_min"],
        "RecentCAGR": recent["CAGRGapVsStrategy15"] >= gate["recent_cagr_gap_vs_strategy15_min"],
        "MDDWithinLimit": full["MDDImprovementVsStrategy15"] >= -gate["full_mdd_deterioration_max"],
        "Rolling3Y": share3 >= gate["positive_3y_rolling_cagr_gap_share_min"],
        "Rolling5Y": share5 >= gate["positive_5y_rolling_cagr_gap_share_min"],
        "Cost3x": stress_indexed.loc["COST_3X", "CAGRGapVsStrategy15"] >= gate["cost3x_cagr_gap_vs_strategy15_min"],
        "Delay1d": stress_indexed.loc["DELAY_1D", "CAGRGapVsStrategy15"] >= gate["delay1d_cagr_gap_vs_strategy15_min"],
        "BeatsStaticEqual": full["CAGRGapVsStaticEqual"] >= gate["full_cagr_gap_vs_static_equal_min"],
        "Breadth": distinct >= gate["minimum_distinct_selected_assets"],
        "NoSelectionDominance": maximum_share <= gate["maximum_single_asset_selection_share"],
    }
    decision = pd.DataFrame([{
        "FullCAGRGapVsStrategy15": full["CAGRGapVsStrategy15"],
        "FullMDDImprovementVsStrategy15": full["MDDImprovementVsStrategy15"],
        "FullCAGRGapVsStaticEqual": full["CAGRGapVsStaticEqual"],
        "Positive3YRollingShare": share3,
        "Positive5YRollingShare": share5,
        "DistinctSelectedAssets": distinct,
        "MaximumSelectionShare": maximum_share,
        **{f"Pass_{name}": bool(value) for name, value in checks.items()},
        "PromotionPass": all(checks.values()),
    }])

    reports = {
        "cross_sectional_risk_momentum_universe": universe,
        "cross_sectional_risk_momentum_summary": summary,
        "cross_sectional_risk_momentum_relative": relative,
        "cross_sectional_risk_momentum_stress": stress,
        "cross_sectional_risk_momentum_activity": activity,
        "cross_sectional_risk_momentum_selection": selection_summary,
        "cross_sectional_risk_momentum_rolling": rolling,
        "cross_sectional_risk_momentum_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_cross_sectional_risk_momentum_validation()
    print(output["cross_sectional_risk_momentum_summary"].loc[
        lambda frame: (frame["Scenario"] == "BASE") & (frame["Window"] == "FULL")
    ].to_string(index=False))
    print("\nRelative")
    print(output["cross_sectional_risk_momentum_relative"].loc[
        lambda frame: frame["Scenario"] == "BASE"
    ].to_string(index=False))
    print("\nSelection")
    print(output["cross_sectional_risk_momentum_selection"].to_string(index=False))
    print("\nDecision")
    print(output["cross_sectional_risk_momentum_decision"].to_string(index=False))
