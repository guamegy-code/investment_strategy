"""Import and validate Nasdaq % above 200MA, then test one transition only."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from attribution import RetirementAllocationAttribution
from backtest import Backtest
from config import DATA_DIR, PROJECT_ROOT, RESULT_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from .point_in_time_signals import (
    load_point_in_time_signal,
    materialize_as_of,
    to_ohlcv_signal,
)


SOURCE = (
    PROJECT_ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)
SIGNAL_PATH = (
    PROJECT_ROOT
    / "data_probability_signals"
    / "NASDAQ_ABOVE_200MA.csv"
)
SOURCE_SYMBOL = "$NAA200R"
METHODOLOGY_VERSION = "stockcharts-naa200r-eod-v1"
BREADTH_TICKER = "BREADTH200"

QQQ_ENTRY_CORE = (
    "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
    "variables.risk_off_score >= 3"
)
SPY_STRESS = "SPY.close < SPY.ema20 and SPY.roc5 <= -1"


@dataclass(frozen=True)
class BreadthProfile:
    name: str
    condition: str | None = None


PROFILES = (
    BreadthProfile("BASELINE_ALIGNED"),
    BreadthProfile("BREADTH_LEVEL_LT30", "BREADTH200.close < 30"),
    BreadthProfile("BREADTH_LEVEL_LT40", "BREADTH200.close < 40"),
    BreadthProfile("BREADTH_LEVEL_LT50", "BREADTH200.close < 50"),
    BreadthProfile(
        "BREADTH_CHANGE20_LE_NEG10",
        "BREADTH200.change20 <= -10",
    ),
)

WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_2011_2020": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
}


def import_stockcharts_export(source_path, output_path=SIGNAL_PATH):
    """Normalize a licensed StockCharts historical export to the PIT contract."""
    raw = pd.read_csv(Path(source_path))
    columns = {str(column).strip().casefold(): column for column in raw.columns}
    date_column = columns.get("date")
    value_column = columns.get("close") or columns.get("value")
    if date_column is None or value_column is None:
        raise ValueError("StockCharts export requires Date and Close columns")
    normalized = pd.DataFrame({
        "ObservationDate": pd.to_datetime(raw[date_column], errors="raise"),
        "AvailableDate": pd.to_datetime(raw[date_column], errors="raise"),
        "Value": pd.to_numeric(raw[value_column], errors="raise"),
        "MethodologyVersion": METHODOLOGY_VERSION,
        "Source": "StockCharts",
        "SourceSymbol": SOURCE_SYMBOL,
    }).sort_values("ObservationDate")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_path, index=False)
    return load_point_in_time_signal(
        output_path,
        minimum=0,
        maximum=100,
        expected_methodology_version=METHODOLOGY_VERSION,
    )


def reference_calendar():
    qqq = pd.read_csv(DATA_DIR / "QQQ.csv", usecols=["Date"])
    return pd.DatetimeIndex(pd.to_datetime(qqq["Date"])).sort_values()


def validate_coverage(signal, calendar=None, max_staleness_days=7):
    """Require coverage of the strategy period without silently stale values."""
    calendar = reference_calendar() if calendar is None else pd.DatetimeIndex(calendar)
    required = calendar[calendar >= pd.Timestamp("2011-01-31")]
    materialized = materialize_as_of(signal, required)
    staleness = materialized.index.to_series() - materialized["AvailableDate"]
    valid = materialized["Value"].notna() & (
        staleness <= pd.Timedelta(days=max_staleness_days)
    )
    start = signal["ObservationDate"].min()
    end = signal["ObservationDate"].max()
    coverage = float(valid.mean()) if len(valid) else 0.0
    issues = []
    if start > required.min():
        issues.append(
            f"history begins {start.date()}, after required {required.min().date()}"
        )
    if end < required.max() - pd.Timedelta(days=max_staleness_days):
        issues.append(
            f"history ends {end.date()}, before required {required.max().date()}"
        )
    if coverage < 0.98:
        issues.append(f"fresh-value coverage is only {coverage:.2%}")
    manifest = {
        "Source": "StockCharts",
        "Symbol": SOURCE_SYMBOL,
        "MethodologyVersion": METHODOLOGY_VERSION,
        "StartDate": start,
        "EndDate": end,
        "Observations": len(signal),
        "FreshValueCoverage": coverage,
        "Status": "PASS" if not issues else "FAIL",
        "Issues": "; ".join(issues),
    }
    if issues:
        raise ValueError("Breadth coverage validation failed: " + "; ".join(issues))
    return manifest


def add_breadth_features(frame):
    result = frame.copy()
    result["CHANGE20"] = result["Close"] - result["Close"].shift(20)
    result["CHANGE20_LAG1"] = result["CHANGE20"].shift(1)
    return result


def _rules(definition):
    return definition["state"]["market_mode"]["rules"]


def _find_rule(definition, source_state, target_state):
    for rule in _rules(definition):
        condition = str(rule.get("when", ""))
        if (
            rule.get("set") == target_state
            and f"state.market_mode == '{source_state}'" in condition
        ):
            return rule
    raise ValueError(f"{source_state} to {target_state} rule was not found")


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    if BREADTH_TICKER not in observations:
        observations.append(BREADTH_TICKER)
    if profile.condition is None:
        return definition
    entry = _find_rule(definition, "BULL", "CAUTION")
    entry["when"] = (
        f"{QQQ_ENTRY_CORE} and "
        f"(({SPY_STRESS}) or ({profile.condition}))"
    )
    return definition


class BreadthBacktest(Backtest):
    def load_one(self, ticker):
        if ticker != BREADTH_TICKER:
            return super().load_one(ticker)
        signal = load_point_in_time_signal(
            SIGNAL_PATH,
            minimum=0,
            maximum=100,
            expected_methodology_version=METHODOLOGY_VERSION,
        )
        return add_breadth_features(
            to_ohlcv_signal(signal, reference_calendar())
        ).dropna(subset=["Close"])


def _run_profile(profile):
    strategy = DeclarativeStrategy(candidate_definition(profile))
    backtest = BreadthBacktest(strategy, tickers=strategy.required_tickers)
    history, trades, rebalances = backtest.run_all()
    attribution = RetirementAllocationAttribution(
        history,
        backtest.data,
        rebalances,
    )
    return {
        "profile": profile,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "events": attribution.transition_events(),
        "quality": attribution.transition_quality(),
    }


def _performance(history, start=None, end=None):
    sample = history.loc[start:end]
    summary = Performance(sample).summary()
    return {
        "StartDate": sample.index.min(),
        "EndDate": sample.index.max(),
        "Observations": len(sample),
        "CAGR": summary["CAGR"],
        "MDD": summary["MDD"],
        "Sharpe": summary["Sharpe"],
        "Calmar": summary["Calmar"],
    }


def _reports(results, manifest):
    window_rows = []
    quality_frames = []
    event_frames = []
    for result in results:
        name = result["profile"].name
        for window, (start, end) in WINDOWS.items():
            row = _performance(result["history"], start, end)
            window_rows.append({"Profile": name, "Window": window, **row})
        for key, frames in (("quality", quality_frames), ("events", event_frames)):
            frame = result[key].copy()
            frame.insert(0, "Profile", name)
            frames.append(frame)
    windows = pd.DataFrame(window_rows)
    quality = pd.concat(quality_frames, ignore_index=True)
    events = pd.concat(event_frames, ignore_index=True)
    baseline = windows.loc[
        (windows["Profile"] == "BASELINE_ALIGNED")
        & (windows["Window"] == "FULL")
    ].iloc[0]
    baseline_recent = windows.loc[
        (windows["Profile"] == "BASELINE_ALIGNED")
        & (windows["Window"] == "RECENT_2021_PRESENT")
    ].iloc[0]
    comparison_rows = []
    for profile in PROFILES[1:]:
        full = windows.loc[
            (windows["Profile"] == profile.name)
            & (windows["Window"] == "FULL")
        ].iloc[0]
        recent = windows.loc[
            (windows["Profile"] == profile.name)
            & (windows["Window"] == "RECENT_2021_PRESENT")
        ].iloc[0]
        target = quality.loc[
            (quality["Profile"] == profile.name)
            & (quality["Transition"] == "BULL->CAUTION")
        ].iloc[0]
        comparison_rows.append({
            "Profile": profile.name,
            "Condition": profile.condition,
            "CAGRGap": full["CAGR"] - baseline["CAGR"],
            "MDDImprovement": full["MDD"] - baseline["MDD"],
            "RecentCAGRGap": recent["CAGR"] - baseline_recent["CAGR"],
            "RecentMDDImprovement": recent["MDD"] - baseline_recent["MDD"],
            "TransitionCount": target["Count"],
            "DirectionalSuccessRate20D": target[
                "DirectionalSuccessRate20D"
            ],
            "AvgQQQForwardMaxDrawdown20D": target[
                "AvgQQQForwardMaxDrawdown20D"
            ],
        })
    return {
        "breadth_200ma_manifest": pd.DataFrame([manifest]),
        "breadth_200ma_windows": windows,
        "breadth_200ma_transition_quality": quality,
        "breadth_200ma_transition_events": events,
        "breadth_200ma_comparison": pd.DataFrame(comparison_rows),
    }


def run_breadth_validation():
    if not SIGNAL_PATH.exists():
        raise FileNotFoundError(
            f"Missing {SIGNAL_PATH}. Export {SOURCE_SYMBOL} daily history "
            "from StockCharts Pro and import it with --import-stockcharts."
        )
    signal = load_point_in_time_signal(
        SIGNAL_PATH,
        minimum=0,
        maximum=100,
        expected_methodology_version=METHODOLOGY_VERSION,
    )
    manifest = validate_coverage(signal)
    results = [_run_profile(profile) for profile in PROFILES]
    reports = _reports(results, manifest)
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--import-stockcharts",
        type=Path,
        help="Path to a licensed $NAA200R historical CSV export.",
    )
    args = parser.parse_args()
    if args.import_stockcharts:
        imported = import_stockcharts_export(args.import_stockcharts)
        print(
            f"Imported {len(imported)} rows to {SIGNAL_PATH} "
            f"({imported['ObservationDate'].min().date()} to "
            f"{imported['ObservationDate'].max().date()})."
        )
    reports = run_breadth_validation()
    print(reports["breadth_200ma_manifest"].to_string(index=False))
    print("\nComparison")
    print(reports["breadth_200ma_comparison"].to_string(index=False))


if __name__ == "__main__":
    main()
