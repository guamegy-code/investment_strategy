"""Pre-registered standalone multi-asset trend-sleeve validation.

This is a deliberately small, research-only proxy for time-series momentum.
It uses repository KRW total-return series across four asset groups and does
not alter Strategy 15 or any production YAML.  The long/short stream is a
synthetic collateralised reference, while the long/flat stream approximates
the direction available to an unlevered retirement-account implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from backtest import Backtest
from config import COMMISSION, DATA_DIR, PROJECT_ROOT, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


START_DATE = pd.Timestamp("2013-01-04")
LOCK_PATH = PROJECT_ROOT / "validation" / "multi_asset_trend_sleeve_lock.json"
ROTATION_DATA_DIR = PROJECT_ROOT / "data_cross_asset_rotation"
STRATEGY15_PATH = PROJECT_ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"

CASH = "KRGOVT3"
GROUPS: dict[str, tuple[str, ...]] = {
    "EQUITY": ("QQQ", "VEA", "VWO", "KOSPI200"),
    "BOND": ("IEF", "KRGOVT10"),
    "COMMODITY": ("GLD",),
    "CURRENCY": ("USDKRW",),
}
ASSETS = tuple(ticker for members in GROUPS.values() for ticker in members)
LOOKBACK = 252
VOL_LOOKBACK = 60
EXECUTION_LAG = 1
BASE_COST = COMMISSION + SLIPPAGE


@dataclass(frozen=True)
class Scenario:
    name: str
    cost_multiple: float = 1.0
    extra_delay_days: int = 0


SCENARIOS = (
    Scenario("BASE"),
    Scenario("COST_3X", cost_multiple=3.0),
    Scenario("DELAY_1D", extra_delay_days=1),
)


def _read_close(path: Path) -> pd.Series:
    frame = pd.read_csv(path, index_col="Date", parse_dates=True)
    if "Close" not in frame or frame.empty:
        raise ValueError(f"missing usable Close data: {path}")
    index = pd.to_datetime(frame.index).tz_localize(None)
    series = pd.Series(frame["Close"].to_numpy(dtype=float), index=index, name=path.stem)
    return series[~series.index.duplicated(keep="last")].sort_index()


def load_prices() -> pd.DataFrame:
    """Load the locked universe and align native-market holidays causally."""

    sources = {
        ticker: ROTATION_DATA_DIR / f"{ticker}.csv"
        for ticker in (*ASSETS, CASH)
        if ticker != "USDKRW"
    }
    sources["USDKRW"] = DATA_DIR / "KRW=X.csv"
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing locked market data: {missing}")

    raw = {ticker: _read_close(path).rename(ticker) for ticker, path in sources.items()}
    last_common = min(series.last_valid_index() for series in raw.values())
    first_common = max(series.first_valid_index() for series in raw.values())
    # Use one executable calendar rather than changing every position on a day
    # where only FX or one local market happened to publish a quote.  Missing
    # foreign/local holiday closes are the last price observable at that time.
    calendar = raw["QQQ"].index
    calendar = calendar[(calendar >= first_common) & (calendar <= last_common)]
    prices = pd.concat(
        [series.reindex(calendar).ffill() for series in raw.values()],
        axis=1,
        sort=False,
    ).dropna()
    prices = prices.loc[~prices.index.duplicated(keep="last")]
    if prices.empty or not set((*ASSETS, CASH)).issubset(prices.columns):
        raise ValueError("locked universe could not be aligned")
    return prices


def _month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    marker = pd.Series(index=index, data=index)
    return pd.DatetimeIndex(marker.groupby(index.to_period("M")).last().to_numpy())


def _group_weights(volatility: pd.Series, *, equal_within_group: bool) -> pd.Series:
    weights = pd.Series(0.0, index=ASSETS)
    group_budget = 1.0 / len(GROUPS)
    for members in GROUPS.values():
        if equal_within_group:
            weights.loc[list(members)] = group_budget / len(members)
            continue
        inverse = 1.0 / volatility.loc[list(members)]
        inverse = inverse.replace([np.inf, -np.inf], np.nan).dropna()
        if inverse.empty or float(inverse.sum()) <= 0.0:
            raise ValueError("invalid inverse-volatility group")
        weights.loc[inverse.index] = group_budget * inverse / inverse.sum()
    return weights


def build_targets(prices: pd.DataFrame, profile: str) -> pd.DataFrame:
    asset_returns = prices.loc[:, ASSETS].pct_change(fill_method=None).fillna(0.0)
    cash_returns = prices[CASH].pct_change(fill_method=None).fillna(0.0)
    excess_returns = asset_returns.sub(cash_returns, axis=0)
    relative_prices = prices.loc[:, ASSETS].div(prices[CASH], axis=0)
    momentum = relative_prices.div(relative_prices.shift(LOOKBACK)) - 1.0
    volatility = excess_returns.rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std()

    rows: dict[pd.Timestamp, pd.Series] = {}
    for date in _month_end_dates(prices.index):
        signal = momentum.loc[date]
        vol = volatility.loc[date]
        if signal.isna().any() or vol.isna().any() or (vol <= 0.0).any():
            continue
        if profile == "EQUAL_GROUP_LONG":
            weights = _group_weights(vol, equal_within_group=True)
            exposure = weights
        else:
            weights = _group_weights(vol, equal_within_group=False)
            if profile == "RISK_PARITY_LONG":
                exposure = weights
            elif profile == "TSMOM_LONG_SHORT":
                exposure = weights * np.sign(signal)
            elif profile == "TSMOM_LONG_FLAT":
                exposure = weights * (signal > 0.0).astype(float)
            else:
                raise ValueError(f"unknown profile: {profile}")
        rows[pd.Timestamp(date)] = exposure.astype(float)
    if not rows:
        raise ValueError(f"no valid monthly targets for {profile}")
    return pd.DataFrame.from_dict(rows, orient="index").loc[:, ASSETS]


def run_stream(
    prices: pd.DataFrame,
    profile: str,
    scenario: Scenario = SCENARIOS[0],
) -> dict[str, pd.DataFrame | str]:
    targets = build_targets(prices, profile)
    lag = EXECUTION_LAG + scenario.extra_delay_days
    positions = targets.reindex(prices.index).shift(lag).ffill().fillna(0.0)
    first_target = positions.abs().sum(axis=1).gt(0.0)
    if not first_target.any():
        raise ValueError(f"{profile} never becomes active")
    first = max(START_DATE, first_target.index[first_target.argmax()])
    positions = positions.loc[first:]
    asset_returns = prices.loc[positions.index, ASSETS].pct_change(fill_method=None).fillna(0.0)
    cash_returns = prices.loc[positions.index, CASH].pct_change(fill_method=None).fillna(0.0)
    excess_returns = asset_returns.sub(cash_returns, axis=0)
    gross_return = cash_returns + (positions * excess_returns).sum(axis=1)
    turnover = positions.diff().abs().sum(axis=1)
    # The validation begins from cash even when the first locked monthly target
    # was formed before START_DATE, so the opening portfolio must pay entry cost.
    turnover.iloc[0] = positions.iloc[0].abs().sum()
    costs = turnover * BASE_COST * scenario.cost_multiple
    net_return = gross_return - costs
    history = pd.DataFrame(index=net_return.index)
    history["Portfolio"] = (1.0 + net_return).cumprod()
    history["TransactionCosts"] = costs.cumsum()
    history["Turnover"] = turnover.cumsum()
    contributions = positions * excess_returns
    return {
        "profile": profile,
        "history": history,
        "positions": positions,
        "contributions": contributions,
    }


def cash_history(prices: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    close = prices[CASH].loc[start:]
    nav = close / close.iloc[0]
    return pd.DataFrame({"Portfolio": nav, "TransactionCosts": 0.0}, index=nav.index)


def _metrics(history: pd.DataFrame) -> dict[str, float]:
    values = Performance(history).summary()
    return {name: float(values[name]) for name in (
        "CAGR", "MDD", "Volatility", "Sharpe", "Sortino", "Calmar", "TransactionCosts"
    )}


def _window_history(history: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    window = history.loc[start:end]
    if len(window) < 2:
        raise ValueError("performance window has fewer than two observations")
    rebased = window.copy()
    rebased["Portfolio"] = rebased["Portfolio"] / rebased["Portfolio"].iloc[0]
    return rebased


def _rolling_excess_share(candidate: pd.DataFrame, cash: pd.DataFrame, years: int = 5) -> float:
    common = candidate.index.intersection(cash.index)
    candidate_nav = candidate.loc[common, "Portfolio"]
    cash_nav = cash.loc[common, "Portfolio"]
    outcomes = []
    for end in _month_end_dates(common):
        cutoff = end - pd.DateOffset(years=years)
        prior = common[common <= cutoff]
        if len(prior) == 0:
            continue
        start = prior[-1]
        if (end - start).days < 365.25 * years * 0.98:
            continue
        candidate_return = candidate_nav.loc[end] / candidate_nav.loc[start] - 1.0
        cash_return = cash_nav.loc[end] / cash_nav.loc[start] - 1.0
        outcomes.append(candidate_return > cash_return)
    return float(np.mean(outcomes)) if outcomes else float("nan")


def _strategy15_history() -> pd.DataFrame:
    definition = load_strategy_definition(STRATEGY15_PATH)
    strategy = DeclarativeStrategy(definition)
    strategy.foreign_asset_tickers = tuple(strategy.holding_tickers)
    strategy.valuation_currency = "KRW"
    strategy.valuation_fx_ticker = "KRW=X"
    strategy.valuation_signal_currency = "LOCAL"
    strategy.required_tickers = tuple(dict.fromkeys((*strategy.required_tickers, "KRW=X")))
    history, _, _ = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        start_date=str(START_DATE.date()),
    ).run_all()
    return history


def _monthly_correlation(left: pd.DataFrame, right: pd.DataFrame) -> float:
    left_monthly = left["Portfolio"].resample("ME").last().pct_change()
    right_monthly = right["Portfolio"].resample("ME").last().pct_change()
    aligned = pd.concat([left_monthly, right_monthly], axis=1, sort=False).dropna()
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def _blend_history(
    baseline: pd.DataFrame,
    sleeve: pd.DataFrame,
    sleeve_weight: float,
) -> pd.DataFrame:
    common = baseline.index.intersection(sleeve.index)
    base_returns = baseline.loc[common, "Portfolio"].pct_change().fillna(0.0)
    sleeve_returns = sleeve.loc[common, "Portfolio"].pct_change().fillna(0.0)
    base_value = 1.0 - sleeve_weight
    sleeve_value = sleeve_weight
    total_cost = 0.0
    rows = []
    prior_period = None
    for date in common:
        period = date.to_period("M")
        if prior_period is not None and period != prior_period:
            total = base_value + sleeve_value
            target_sleeve = total * sleeve_weight
            traded = abs(target_sleeve - sleeve_value)
            cost = traded * BASE_COST
            total_cost += cost
            total -= cost
            sleeve_value = total * sleeve_weight
            base_value = total - sleeve_value
        base_value *= 1.0 + float(base_returns.loc[date])
        sleeve_value *= 1.0 + float(sleeve_returns.loc[date])
        rows.append((date, base_value + sleeve_value, total_cost))
        prior_period = period
    return pd.DataFrame(rows, columns=("Date", "Portfolio", "TransactionCosts")).set_index("Date")


def _assert_lock() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    expected_groups = {group: list(members) for group, members in GROUPS.items()}
    checks = {
        "start_date": lock["data"]["start_date"] == str(START_DATE.date()),
        "cash": lock["data"]["cash_ticker"] == CASH,
        "groups": lock["data"]["asset_groups"] == expected_groups,
        "momentum": lock["construction"]["momentum_lookback_days"] == LOOKBACK,
        "volatility": lock["construction"]["volatility_lookback_days"] == VOL_LOOKBACK,
        "lag": lock["construction"]["execution_lag_days"] == EXECUTION_LAG,
        "cost": np.isclose(lock["construction"]["base_one_way_cost"], BASE_COST),
    }
    if not all(checks.values()):
        raise AssertionError(f"implementation differs from pre-registration lock: {checks}")
    return lock


def run_multi_asset_trend_validation() -> dict[str, pd.DataFrame]:
    lock = _assert_lock()
    prices = load_prices()
    strategy15 = _strategy15_history()
    profiles = ("EQUAL_GROUP_LONG", "RISK_PARITY_LONG", "TSMOM_LONG_SHORT", "TSMOM_LONG_FLAT")
    base_streams = {profile: run_stream(prices, profile) for profile in profiles}
    first = min(result["history"].index.min() for result in base_streams.values())
    cash = cash_history(prices, first)

    windows = {
        "FULL": (None, None),
        "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
        "RECENT_2021_PRESENT": ("2021-01-01", None),
    }
    summary_rows = []
    cash_metrics_by_window = {}
    for window, (start, end) in windows.items():
        cash_window = _window_history(cash, start, end)
        cash_values = _metrics(cash_window)
        cash_metrics_by_window[window] = cash_values
        summary_rows.append({"Scenario": "BASE", "Strategy": "CASH", "Window": window, **cash_values})
        for profile, result in base_streams.items():
            history = _window_history(result["history"], start, end)
            summary_rows.append({"Scenario": "BASE", "Strategy": profile, "Window": window, **_metrics(history)})
    summary = pd.DataFrame(summary_rows)
    relative_rows = []
    indexed_summary = summary.set_index(["Strategy", "Window"])
    for window in windows:
        cash_row = indexed_summary.loc[("CASH", window)]
        risk_parity_row = indexed_summary.loc[("RISK_PARITY_LONG", window)]
        for profile in profiles:
            row = indexed_summary.loc[(profile, window)]
            relative_rows.append({
                "Strategy": profile,
                "Window": window,
                "CAGRGapVsCash": row["CAGR"] - cash_row["CAGR"],
                "CAGRGapVsRiskParity": row["CAGR"] - risk_parity_row["CAGR"],
                "MDDImprovementVsRiskParity": row["MDD"] - risk_parity_row["MDD"],
                "SharpeGapVsRiskParity": row["Sharpe"] - risk_parity_row["Sharpe"],
            })
    relative = pd.DataFrame(relative_rows)

    stress_rows = []
    for scenario in SCENARIOS:
        for profile in ("TSMOM_LONG_SHORT", "TSMOM_LONG_FLAT"):
            result = base_streams[profile] if scenario.name == "BASE" else run_stream(prices, profile, scenario)
            values = _metrics(result["history"])
            cash_values = _metrics(_window_history(cash, str(result["history"].index.min().date()), None))
            stress_rows.append({
                "Scenario": scenario.name,
                "Strategy": profile,
                **values,
                "ExcessCAGR": values["CAGR"] - cash_values["CAGR"],
            })
    stress = pd.DataFrame(stress_rows)

    correlations = []
    for profile in ("TSMOM_LONG_SHORT", "TSMOM_LONG_FLAT"):
        correlations.append({
            "Strategy": profile,
            "MonthlyCorrelationToStrategy15": _monthly_correlation(base_streams[profile]["history"], strategy15),
            "Positive5YRollingExcessShare": _rolling_excess_share(base_streams[profile]["history"], cash),
        })
    independence = pd.DataFrame(correlations)

    contribution_rows = []
    for profile in ("TSMOM_LONG_SHORT", "TSMOM_LONG_FLAT"):
        contributions = base_streams[profile]["contributions"]
        for group, members in GROUPS.items():
            contribution_rows.append({
                "Strategy": profile,
                "AssetGroup": group,
                "ArithmeticContribution": float(contributions.loc[:, list(members)].sum().sum()),
            })
    contributions = pd.DataFrame(contribution_rows)

    blend_rows = []
    blend_histories = {}
    for profile in ("TSMOM_LONG_SHORT", "TSMOM_LONG_FLAT"):
        for weight in (0.05, 0.10):
            label = f"STRATEGY15_{int((1-weight)*100)}_{profile}_{int(weight*100)}"
            history = _blend_history(strategy15, base_streams[profile]["history"], weight)
            blend_histories[label] = history
            values = _metrics(history)
            common_baseline = _window_history(strategy15, str(history.index.min().date()), str(history.index.max().date()))
            reference = _metrics(common_baseline)
            blend_rows.append({
                "Blend": label,
                "Sleeve": profile,
                "SleeveWeight": weight,
                **values,
                "CAGRGap": values["CAGR"] - reference["CAGR"],
                "MDDImprovement": values["MDD"] - reference["MDD"],
            })
    blends = pd.DataFrame(blend_rows)

    base_indexed = summary.loc[summary["Scenario"] == "BASE"].set_index(["Strategy", "Window"])
    stress_indexed = stress.set_index(["Strategy", "Scenario"])
    independent_indexed = independence.set_index("Strategy")
    positive_groups = contributions.loc[contributions["ArithmeticContribution"] > 0].groupby("Strategy").size()
    research_gate = lock["promotion_gates"]["research_reference"]
    implementable_gate = lock["promotion_gates"]["implementable_proxy"]

    def excess(profile: str, window: str) -> float:
        return float(base_indexed.loc[(profile, window), "CAGR"] - base_indexed.loc[("CASH", window), "CAGR"])

    ls = "TSMOM_LONG_SHORT"
    lf = "TSMOM_LONG_FLAT"
    ls_checks = {
        "FullExcessCAGR": excess(ls, "FULL") >= research_gate["full_excess_cagr_min"],
        "DevelopmentExcessCAGR": excess(ls, "DEVELOPMENT_PRE2021") >= research_gate["development_excess_cagr_min"],
        "RecentExcessCAGR": excess(ls, "RECENT_2021_PRESENT") >= research_gate["recent_excess_cagr_min"],
        "LowCorrelation": independent_indexed.loc[ls, "MonthlyCorrelationToStrategy15"] <= research_gate["monthly_correlation_to_strategy15_max"],
        "RollingConsistency": independent_indexed.loc[ls, "Positive5YRollingExcessShare"] >= research_gate["positive_5y_rolling_excess_share_min"],
        "BroadContribution": int(positive_groups.get(ls, 0)) >= research_gate["positive_contributing_asset_groups_min"],
        "Cost3x": stress_indexed.loc[(ls, "COST_3X"), "ExcessCAGR"] >= research_gate["cost3x_excess_cagr_min"],
        "Delay1d": stress_indexed.loc[(ls, "DELAY_1D"), "ExcessCAGR"] >= research_gate["delay1d_excess_cagr_min"],
    }
    lf_blend = blends.loc[(blends["Sleeve"] == lf) & np.isclose(blends["SleeveWeight"], implementable_gate["strategy15_blend_weight"])].iloc[0]
    lf_checks = {
        "FullExcessCAGR": excess(lf, "FULL") >= implementable_gate["full_excess_cagr_min"],
        "LowCorrelation": independent_indexed.loc[lf, "MonthlyCorrelationToStrategy15"] <= implementable_gate["monthly_correlation_to_strategy15_max"],
        "BlendRaisesCAGR": lf_blend["CAGRGap"] >= implementable_gate["blend_cagr_gap_min"],
        "BlendMDDWithinLimit": lf_blend["MDDImprovement"] >= -implementable_gate["blend_mdd_deterioration_max"],
    }
    decision = pd.DataFrame([{
        **{f"Research_{name}": bool(value) for name, value in ls_checks.items()},
        "ResearchReferencePass": all(ls_checks.values()),
        **{f"Implementable_{name}": bool(value) for name, value in lf_checks.items()},
        "ImplementableProxyPass": all(lf_checks.values()),
        "ResearchCAGRGapVsRiskParity": float(
            base_indexed.loc[(ls, "FULL"), "CAGR"]
            - base_indexed.loc[("RISK_PARITY_LONG", "FULL"), "CAGR"]
        ),
        "ImplementableCAGRGapVsRiskParity": float(
            base_indexed.loc[(lf, "FULL"), "CAGR"]
            - base_indexed.loc[("RISK_PARITY_LONG", "FULL"), "CAGR"]
        ),
        # ResearchReferencePass means the synthetic stream is a viable
        # independent reference, not that it should replace Strategy 15.
        "ProductionPromotionPass": all(lf_checks.values()),
    }])

    activity_rows = []
    for profile in (ls, lf):
        positions = base_streams[profile]["positions"]
        changes = positions.diff().abs().sum(axis=1) > 1e-12
        for date in positions.index[changes]:
            activity_rows.append({
                "Strategy": profile,
                "Date": date,
                "GrossExposure": float(positions.loc[date].abs().sum()),
                "NetExposure": float(positions.loc[date].sum()),
                "LongAssets": int((positions.loc[date] > 0).sum()),
                "ShortAssets": int((positions.loc[date] < 0).sum()),
            })

    reports = {
        "multi_asset_trend_summary": summary,
        "multi_asset_trend_relative": relative,
        "multi_asset_trend_stress": stress,
        "multi_asset_trend_independence": independence,
        "multi_asset_trend_contributions": contributions,
        "multi_asset_trend_blends": blends,
        "multi_asset_trend_activity": pd.DataFrame(activity_rows),
        "multi_asset_trend_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_multi_asset_trend_validation()
    print(output["multi_asset_trend_summary"].to_string(index=False))
    print("\nIndependence")
    print(output["multi_asset_trend_independence"].to_string(index=False))
    print("\nBlends")
    print(output["multi_asset_trend_blends"].to_string(index=False))
    print("\nDecision")
    print(output["multi_asset_trend_decision"].to_string(index=False))
