"""Validate a KRW cross-asset rotation inside strategy 15's BIL sleeve.

Strategy 15 remains the source of truth for state transitions and for the
QQQ/TDF targets.  This experiment replaces *only* its BIL target with a
monthly, constrained selection of cross-asset candidates.  The source
strategy is evaluated on its existing USD data first; that causal schedule is
then replayed against KRW total-return prices, so adding Korean assets cannot
change the production strategy's state or TDF allocation.

This is intentionally a research-only PoC.  It is not a production strategy
definition and a failed validation must not modify strategy 15.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Mapping
import json

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import Backtest
from config import (
    COMMISSION,
    DATA_DIR,
    GENERAL_COMPARISON_START_DATE,
    PROJECT_ROOT,
    RESULT_DIR,
    SLIPPAGE,
)
from indicators import Indicator
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
ROTATION_DATA_DIR = PROJECT_ROOT / "data_cross_asset_rotation"
START_DATE = GENERAL_COMPARISON_START_DATE

QQQ = "QQQ"
TDF = "TDF2050_PROXY"
BIL = "BIL"
SPY = "SPY"
CORE_HOLDINGS = (QQQ, TDF, BIL)

# The initial universe is deliberately broad by asset class but narrow by
# product count.  Country-specific equity ETFs are a later, separately gated
# experiment; including many of them now would create a multiple-testing bias.
@dataclass(frozen=True)
class CandidateSpec:
    ticker: str
    source_ticker: str
    asset_group: str
    asset_class: str
    is_foreign: bool


CANDIDATES = (
    CandidateSpec("GLD", "GLD", "GOLD", "GOLD", True),
    CandidateSpec("SHY", "SHY", "US_SHORT_BOND", "BOND", True),
    CandidateSpec("IEF", "IEF", "US_INTERMEDIATE_BOND", "BOND", True),
    CandidateSpec("KRGOVT3", "114100.KS", "KR_SHORT_BOND", "BOND", False),
    CandidateSpec("KRGOVT10", "148070.KS", "KR_INTERMEDIATE_BOND", "BOND", False),
    CandidateSpec("KOSPI200", "069500.KS", "KR_EQUITY", "EQUITY", False),
    CandidateSpec("VEA", "VEA", "DEVELOPED_EQUITY", "EQUITY", True),
    CandidateSpec("VWO", "VWO", "EM_EQUITY", "EQUITY", True),
)

ALL_HOLDINGS = (*CORE_HOLDINGS, *(spec.ticker for spec in CANDIDATES))
REBALANCE_BAND = 0.075
MAX_SELECTED_ASSETS = 2
MAX_SINGLE_SLEEVE_SHARE = 0.50
MAX_GOLD_SLEEVE_SHARE = 0.30
MAX_EQUITY_SLEEVE_SHARE = 0.30
EPSILON = 1e-9

WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
}


@dataclass(frozen=True)
class StressScenario:
    name: str
    cost_multiple: float = 1.0
    signal_delay_days: int = 0


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
)


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep a clean OHLCV frame suitable for the shared backtest loader."""

    if frame.empty:
        raise ValueError("received an empty market-data frame")
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame.index.name = "Date"
    if "Close" not in frame:
        raise ValueError("market data is missing Close")
    for column in ("Open", "High", "Low"):
        if column not in frame:
            frame[column] = frame["Close"]
    if "Volume" not in frame:
        frame["Volume"] = 0.0
    return frame[["Close", "High", "Low", "Open", "Volume"]].dropna(
        subset=["Close"]
    ).sort_index()


def _read_local(ticker: str) -> pd.DataFrame:
    path = DATA_DIR / f"{ticker}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Required local data is unavailable: {path}. "
            "Run the normal market-data refresh before this validation."
        )
    return _normalise_frame(pd.read_csv(path, index_col="Date", parse_dates=True))


def _download(ticker: str) -> pd.DataFrame:
    frame = yf.download(
        ticker,
        start="2010-01-01",
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )
    if frame.empty:
        raise ValueError(f"No market data downloaded for {ticker}")
    return _normalise_frame(frame)


def _source_frame(logical_ticker: str, source_ticker: str) -> pd.DataFrame:
    """Prefer repository data to preserve reproducibility and avoid downloads."""

    local_path = DATA_DIR / f"{logical_ticker}.csv"
    if local_path.exists():
        return _read_local(logical_ticker)
    return _download(source_ticker)


def _convert_to_krw(frame: pd.DataFrame, fx_close: pd.Series) -> pd.DataFrame:
    """Convert an unhedged foreign total-return price series to KRW."""

    # A few US ETF files begin one business day before the FX source.  The
    # validation itself starts in 2012, so dropping only that unavailable
    # pre-history avoids inventing an FX value while retaining ample indicator
    # warm-up data.
    first_fx_date = fx_close.first_valid_index()
    if first_fx_date is None:
        raise ValueError("USD/KRW source contains no usable observations")
    converted = frame.loc[frame.index >= first_fx_date].copy()
    fx = fx_close.reindex(converted.index).ffill()
    if fx.isna().any():
        first_missing = fx.index[fx.isna()][0]
        raise ValueError(f"KRW/USD data is unavailable for {first_missing.date()}")
    for column in ("Close", "High", "Low", "Open"):
        converted[column] = converted[column] * fx
    return converted


def build_rotation_data(
    output_dir: Path = ROTATION_DATA_DIR,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Build KRW total-return prices for the initial rotation universe.

    QQQ/TDF/BIL and all foreign candidates are transformed with USD/KRW.  The
    Korean ETF candidates are already KRW-priced.  The production state schedule
    remains USD-based and is intentionally built separately.
    """

    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.csv"
    expected = [output_dir / f"{ticker}.csv" for ticker in ALL_HOLDINGS]
    if not refresh and manifest_path.exists() and all(path.exists() for path in expected):
        return pd.read_csv(manifest_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))

    fx = _read_local("KRW=X")["Close"]
    frames: dict[str, pd.DataFrame] = {
        QQQ: _read_local(QQQ),
        TDF: _read_local(TDF),
        BIL: _read_local(BIL),
    }
    source_by_ticker = {QQQ: QQQ, TDF: TDF, BIL: BIL}
    foreign_by_ticker = {QQQ: True, TDF: True, BIL: True}
    spec_by_ticker = {spec.ticker: spec for spec in CANDIDATES}
    for spec in CANDIDATES:
        frames[spec.ticker] = _source_frame(spec.ticker, spec.source_ticker)
        source_by_ticker[spec.ticker] = spec.source_ticker
        foreign_by_ticker[spec.ticker] = spec.is_foreign

    manifest_rows = []
    for ticker in ALL_HOLDINGS:
        frame = frames[ticker]
        is_foreign = foreign_by_ticker[ticker]
        prepared = _convert_to_krw(frame, fx) if is_foreign else frame
        prepared = Indicator.add_indicators(prepared)
        prepared.to_csv(output_dir / f"{ticker}.csv")
        spec = spec_by_ticker.get(ticker)
        manifest_rows.append({
            "Ticker": ticker,
            "SourceTicker": source_by_ticker[ticker],
            "CurrencyBasis": "KRW_UNHEDGED" if is_foreign else "KRW_LOCAL",
            "AssetGroup": spec.asset_group if spec else "CORE",
            "AssetClass": spec.asset_class if spec else "CORE",
            "StartDate": prepared.index.min(),
            "EndDate": prepared.index.max(),
            "Observations": len(prepared),
        })
    manifest = pd.DataFrame(manifest_rows)
    manifest.to_csv(manifest_path, index=False)
    return manifest


class Strategy15ScheduleRecorder(DeclarativeStrategy):
    """Record strategy 15's causal targets without changing its behavior."""

    def __init__(self, definition: Mapping[str, Any], record: Callable):
        super().__init__(definition)
        self._record = record

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        self._record(pd.Timestamp(date), {
            "state": self.state,
            "target": signal["target"].copy(),
            "rebalance": bool(signal["rebalance"]),
            "days": int(signal["days"]),
            "reason": signal.get("reason"),
            "risk_off_score": getattr(self, "risk_off_score", None),
            "recovery_score": getattr(self, "recovery_score", None),
        })
        return signal


def build_strategy15_schedule() -> dict[pd.Timestamp, dict[str, Any]]:
    """Evaluate the unmodified USD strategy 15 once and retain its schedule."""

    schedule: dict[pd.Timestamp, dict[str, Any]] = {}
    definition = load_strategy_definition(SOURCE)
    strategy = Strategy15ScheduleRecorder(definition, schedule.__setitem__)
    backtest = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        start_date=START_DATE,
    )
    backtest.run_all()
    if not schedule:
        raise ValueError("strategy 15 did not produce a schedule")
    return schedule


def _target_deviation(
    portfolio: Any,
    prices: Mapping[str, float],
    target: Mapping[str, float],
) -> float:
    current = portfolio.weights(prices)
    return max(
        abs(float(current.get(ticker, 0.0)) - float(weight))
        for ticker, weight in target.items()
    )


class Strategy15KrwSchedule:
    """Replay the locked strategy-15 targets on KRW total-return prices."""

    def __init__(
        self,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        required_tickers: tuple[str, ...],
    ):
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self.strategy_id = "poc:strategy15-krw-schedule"
        self.display_name = "BASELINE"
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = CORE_HOLDINGS
        self.observation_tickers = ()
        self.required_tickers = required_tickers
        self.required_market_fields: dict[str, tuple[str, ...]] = {}
        self.risk_asset_tickers = (QQQ,)
        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None

    def evaluate(self, date, market, portfolio):
        point = self._point_for_date(date)
        self.state = point["state"]
        self.target = dict(point["target"])
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = BIL if self.target[BIL] > 0.0 else None
        return {
            "rebalance": bool(point["rebalance"]),
            "target": self.target.copy(),
            "days": int(point["days"]),
            "reason": point["reason"],
        }


class StateConditionedCrossAssetRotation:
    """Replace strategy 15's BIL sleeve with a constrained monthly selection."""

    def __init__(
        self,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        required_tickers: tuple[str, ...],
        record: Callable[[dict[str, Any]], None],
    ):
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self._record = record
        self.strategy_id = "poc:strategy15-state-conditioned-cross-asset-rotation"
        self.display_name = "STATE_CONDITIONED_CROSS_ASSET_ROTATION"
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = required_tickers
        self.observation_tickers = ()
        self.required_tickers = required_tickers
        self.required_market_fields: dict[str, tuple[str, ...]] = {}
        self.risk_asset_tickers = (QQQ,)
        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.last_selection_month = None
        self.active_mix = {BIL: 1.0}
        self.selected: tuple[str, ...] = ()
        self.last_scores: dict[str, float] = {}

    @staticmethod
    def _finite(*values: Any) -> bool:
        try:
            return all(value is not None and isfinite(float(value)) for value in values)
        except (TypeError, ValueError):
            return False

    def _score_candidates(
        self,
        market: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, float]:
        """Return absolute-momentum candidates that beat USD cash in KRW."""

        cash = market[BIL]
        cash_values = (cash.get("ROC60"), cash.get("ROC120"), cash.get("ROC252"))
        if not self._finite(*cash_values):
            return {}
        cash60, cash120, cash252 = (float(value) for value in cash_values)
        scores: dict[str, float] = {}
        for spec in CANDIDATES:
            asset = market[spec.ticker]
            values = (
                asset.get("Close"),
                asset.get("EMA200"),
                asset.get("ROC60"),
                asset.get("ROC120"),
                asset.get("ROC252"),
                asset.get("VOL60"),
            )
            if not self._finite(*values):
                continue
            close, ema200, roc60, roc120, roc252, volatility = (
                float(value) for value in values
            )
            # An asset must have a positive long trend and beat cash over the
            # most recent quarter.  Volatility controls position size, not the
            # ranking, so that high-volatility equity does not win by signal
            # construction alone.
            if close <= ema200 or roc60 <= cash60 or volatility <= 0.0:
                continue
            scores[spec.ticker] = (
                0.50 * (roc60 - cash60)
                + 0.30 * (roc120 - cash120)
                + 0.20 * (roc252 - cash252)
            )
        return scores

    def _select_mix(
        self,
        market: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, float], tuple[str, ...], dict[str, float]]:
        scores = self._score_candidates(market)
        best_by_group: dict[str, str] = {}
        for spec in CANDIDATES:
            ticker = spec.ticker
            if ticker not in scores:
                continue
            incumbent = best_by_group.get(spec.asset_group)
            if incumbent is None or scores[ticker] > scores[incumbent]:
                best_by_group[spec.asset_group] = ticker

        selected = tuple(sorted(
            best_by_group.values(), key=lambda ticker: scores[ticker], reverse=True
        )[:MAX_SELECTED_ASSETS])
        mix = {BIL: 1.0}
        if not selected:
            return mix, selected, scores

        inverse_volatility = {
            ticker: 1.0 / float(market[ticker]["VOL60"])
            for ticker in selected
        }
        inverse_total = sum(inverse_volatility.values())
        allocated = 0.0
        equity_allocated = 0.0
        by_ticker = {spec.ticker: spec for spec in CANDIDATES}
        for ticker in selected:
            spec = by_ticker[ticker]
            capacity = MAX_SINGLE_SLEEVE_SHARE
            if spec.asset_class == "GOLD":
                capacity = min(capacity, MAX_GOLD_SLEEVE_SHARE)
            if spec.asset_class == "EQUITY":
                capacity = min(
                    capacity,
                    max(0.0, MAX_EQUITY_SLEEVE_SHARE - equity_allocated),
                )
            raw_share = inverse_volatility[ticker] / inverse_total
            share = min(raw_share, capacity)
            if share <= EPSILON:
                continue
            mix[ticker] = share
            allocated += share
            if spec.asset_class == "EQUITY":
                equity_allocated += share
        mix[BIL] = max(0.0, 1.0 - allocated)
        return mix, selected, scores

    def _rotation_target(
        self,
        base_target: Mapping[str, float],
    ) -> dict[str, float]:
        sleeve = float(base_target.get(BIL, 0.0))
        target = {ticker: 0.0 for ticker in self.holding_tickers}
        target[QQQ] = float(base_target[QQQ])
        target[TDF] = float(base_target[TDF])
        for ticker, share in self.active_mix.items():
            target[ticker] = target.get(ticker, 0.0) + sleeve * float(share)
        total = sum(target.values())
        if not np.isclose(total, 1.0):
            target[BIL] += 1.0 - total
        return {ticker: round(max(0.0, weight), 10) for ticker, weight in target.items()}

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        base_target = point["target"]
        sleeve_active = float(base_target[BIL]) > EPSILON
        selection_changed = False
        selection_event = False
        if sleeve_active:
            month = timestamp.to_period("M")
            if self.last_selection_month != month:
                prior_mix = self.active_mix.copy()
                self.active_mix, self.selected, self.last_scores = self._select_mix(market)
                self.last_selection_month = month
                selection_changed = self.active_mix != prior_mix
                selection_event = True
        elif self.active_mix != {BIL: 1.0}:
            self.active_mix = {BIL: 1.0}
            self.selected = ()
            self.last_scores = {}
            self.last_selection_month = None
            selection_changed = True

        target = self._rotation_target(base_target)
        if not (
            np.isclose(target[QQQ], float(base_target[QQQ]))
            and np.isclose(target[TDF], float(base_target[TDF]))
        ):
            raise AssertionError(
                "cross-asset rotation must not alter strategy 15 QQQ or TDF targets"
            )
        prices = {ticker: market[ticker]["Close"] for ticker in self.holding_tickers}
        deviation = _target_deviation(portfolio, prices, target)
        baseline_rebalance = bool(point["rebalance"])
        rebalance = baseline_rebalance or selection_changed
        reason = point["reason"] if baseline_rebalance else None
        if selection_changed:
            selection_reason = "MONTHLY_CROSS_ASSET_ROTATION"
            reason = f"{reason}|{selection_reason}" if reason else selection_reason

        self.state = point["state"]
        self.target = target
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = BIL if target[BIL] > EPSILON else None
        if selection_event or selection_changed:
            self._record({
                "Date": timestamp,
                "ProductionState": point["state"],
                "BILSleeve": float(base_target[BIL]),
                "Selected": ",".join(self.selected),
                "Mix": json.dumps(self.active_mix, sort_keys=True),
                "Scores": json.dumps(self.last_scores, sort_keys=True),
                "TargetDeviation": deviation,
            })
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": int(point["days"]),
            "reason": reason,
        }


def _run_portfolio(
    strategy: Any,
    *,
    data_dir: Path,
    scenario: StressScenario,
) -> dict[str, Any]:
    backtest = Backtest(
        strategy,
        data_dir=data_dir,
        tickers=strategy.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.signal_delay_days,
        start_date=START_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "label": strategy.display_name,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }


def _performance_row(result: Mapping[str, Any], window: str, start, end) -> dict | None:
    history = result["history"].loc[start:end]
    if len(history) < 2:
        return None
    metrics = Performance(history).summary()
    return {
        "Strategy": result["label"],
        "Window": window,
        "StartDate": history.index.min(),
        "EndDate": history.index.max(),
        "Observations": len(history),
        "TotalReturn": history["Portfolio"].iloc[-1] / history["Portfolio"].iloc[0] - 1.0,
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Volatility": metrics["Volatility"],
        "Sharpe": metrics["Sharpe"],
        "Sortino": metrics["Sortino"],
        "Calmar": metrics["Calmar"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Rebalances": len(result["rebalances"]),
        "Trades": len(result["trades"]),
    }


def _performance_report(results: list[Mapping[str, Any]]) -> pd.DataFrame:
    rows = []
    for result in results:
        for window, (start, end) in WINDOWS.items():
            row = _performance_row(result, window, start, end)
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def _relative_report(report: pd.DataFrame) -> pd.DataFrame:
    baseline = report.loc[report["Strategy"] == "BASELINE"].set_index("Window")
    candidate = report.loc[report["Strategy"] != "BASELINE"]
    rows = []
    for _, row in candidate.iterrows():
        reference = baseline.loc[row["Window"]]
        rows.append({
            "Strategy": row["Strategy"],
            "Window": row["Window"],
            "CAGRGap": row["CAGR"] - reference["CAGR"],
            "MDDImprovement": row["MDD"] - reference["MDD"],
            "SharpeGap": row["Sharpe"] - reference["Sharpe"],
            "CalmarGap": row["Calmar"] - reference["Calmar"],
            "TransactionCostGap": row["TransactionCosts"] - reference["TransactionCosts"],
            "RebalanceGap": row["Rebalances"] - reference["Rebalances"],
        })
    return pd.DataFrame(rows)


def _stress_report(
    schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
    data_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    required_tickers = tuple(ALL_HOLDINGS)
    for scenario in STRESS_SCENARIOS:
        activity: list[dict[str, Any]] = []
        baseline = Strategy15KrwSchedule(schedule, required_tickers)
        candidate = StateConditionedCrossAssetRotation(
            schedule, required_tickers, activity.append
        )
        report = _performance_report([
            _run_portfolio(baseline, data_dir=data_dir, scenario=scenario),
            _run_portfolio(candidate, data_dir=data_dir, scenario=scenario),
        ])
        report.insert(0, "Scenario", scenario.name)
        rows.append(report.loc[report["Window"] == "FULL"])
    stress = pd.concat(rows, ignore_index=True)
    relative_rows = []
    for scenario, group in stress.groupby("Scenario"):
        baseline = group.loc[group["Strategy"] == "BASELINE"].iloc[0]
        candidate = group.loc[group["Strategy"] != "BASELINE"].iloc[0]
        relative_rows.append({
            "Scenario": scenario,
            "CAGRGap": candidate["CAGR"] - baseline["CAGR"],
            "MDDImprovement": candidate["MDD"] - baseline["MDD"],
            "CalmarGap": candidate["Calmar"] - baseline["Calmar"],
            "TransactionCostGap": candidate["TransactionCosts"] - baseline["TransactionCosts"],
        })
    return stress, pd.DataFrame(relative_rows)


def _decision_report(relative: pd.DataFrame, stress_relative: pd.DataFrame) -> pd.DataFrame:
    indexed = relative.set_index("Window")
    full = indexed.loc["FULL"]
    development = indexed.loc["DEVELOPMENT_PRE2021"]
    recent = indexed.loc["RECENT_2021_PRESENT"]
    cost3 = stress_relative.set_index("Scenario").loc["COST_3X"]
    delay1 = stress_relative.set_index("Scenario").loc["DELAY_1D"]
    # These gates are set before examining the results.  A small CAGR give-up
    # is acceptable only when downside risk improves both before and after the
    # OOS boundary and survives basic cost/execution stress.
    risk_efficient = bool(
        full["CAGRGap"] >= -0.003
        and full["MDDImprovement"] >= 0.01
        and full["CalmarGap"] >= 0.0
    )
    period_consistent = bool(
        development["MDDImprovement"] >= 0.0
        and recent["MDDImprovement"] >= 0.0
    )
    stress_survives = bool(
        cost3["CAGRGap"] >= -0.003
        and delay1["CAGRGap"] >= -0.003
        and cost3["MDDImprovement"] >= 0.0
        and delay1["MDDImprovement"] >= 0.0
    )
    return pd.DataFrame([{
        "Strategy": "STATE_CONDITIONED_CROSS_ASSET_ROTATION",
        "FullCAGRGap": full["CAGRGap"],
        "FullMDDImprovement": full["MDDImprovement"],
        "FullCalmarGap": full["CalmarGap"],
        "DevelopmentMDDImprovement": development["MDDImprovement"],
        "RecentMDDImprovement": recent["MDDImprovement"],
        "Cost3xCAGRGap": cost3["CAGRGap"],
        "Delay1dCAGRGap": delay1["CAGRGap"],
        "RiskEfficientPass": risk_efficient,
        "PeriodConsistencyPass": period_consistent,
        "StressSurvivalPass": stress_survives,
        "PoCPass": risk_efficient and period_consistent and stress_survives,
    }])


def run_state_conditioned_cross_asset_rotation_validation(
    *,
    refresh_data: bool = False,
) -> dict[str, pd.DataFrame]:
    """Build data, run the locked PoC, and write reproducible CSV reports."""

    universe = build_rotation_data(refresh=refresh_data)
    schedule = build_strategy15_schedule()
    activity: list[dict[str, Any]] = []
    required_tickers = tuple(ALL_HOLDINGS)
    base_scenario = STRESS_SCENARIOS[0]
    baseline = Strategy15KrwSchedule(schedule, required_tickers)
    candidate = StateConditionedCrossAssetRotation(
        schedule, required_tickers, activity.append
    )
    results = [
        _run_portfolio(baseline, data_dir=ROTATION_DATA_DIR, scenario=base_scenario),
        _run_portfolio(candidate, data_dir=ROTATION_DATA_DIR, scenario=base_scenario),
    ]
    summary = _performance_report(results)
    relative = _relative_report(summary)
    stress, stress_relative = _stress_report(schedule, ROTATION_DATA_DIR)
    selection = pd.DataFrame(activity)
    decision = _decision_report(relative, stress_relative)
    reports = {
        "state_conditioned_cross_asset_rotation_universe": universe,
        "state_conditioned_cross_asset_rotation_summary": summary,
        "state_conditioned_cross_asset_rotation_relative": relative,
        "state_conditioned_cross_asset_rotation_selection": selection,
        "state_conditioned_cross_asset_rotation_stress": stress,
        "state_conditioned_cross_asset_rotation_stress_relative": stress_relative,
        "state_conditioned_cross_asset_rotation_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    reports = run_state_conditioned_cross_asset_rotation_validation()
    print(reports["state_conditioned_cross_asset_rotation_summary"].to_string(index=False))
    print("\nRelative to strategy 15 on KRW total-return prices")
    print(reports["state_conditioned_cross_asset_rotation_relative"].to_string(index=False))
    print("\nDecision")
    print(reports["state_conditioned_cross_asset_rotation_decision"].to_string(index=False))
