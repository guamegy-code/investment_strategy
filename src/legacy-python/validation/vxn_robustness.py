"""Falsification-focused robustness checks for the state-specific VXN gate."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from attribution import RetirementAllocationAttribution
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy
from .vxn_state_gates import (
    PROFILES as ORIGINAL_PROFILES,
    VxnGateProfile,
    VxnStateBacktest,
    _find_rule,
    candidate_definition,
    download_vxn,
)


THRESHOLDS = (-2.5, 0.0, 2.5, 5.0, 7.5, 10.0, 12.5)
FOLDS = (
    ("2011_2016_TO_2017_2018", "2016-12-31", "2017-01-01", "2018-12-31"),
    ("2011_2018_TO_2019_2020", "2018-12-31", "2019-01-01", "2020-12-31"),
    ("2011_2020_TO_2021_2022", "2020-12-31", "2021-01-01", "2022-12-31"),
    ("2011_2022_TO_2023_2024", "2022-12-31", "2023-01-01", "2024-12-31"),
    ("2011_2024_TO_2025_2026", "2024-12-31", "2025-01-01", "2026-12-31"),
)
BASE_FEATURES = (
    "RiskOffScore",
    "QQQ_ROC5",
    "QQQ_ROC20",
    "SPY_ROC5",
)


def threshold_name(threshold):
    text = f"{threshold:g}".replace("-", "NEG").replace(".", "P")
    return f"VXN_ROC5_GE_{text}"


def threshold_definition(threshold=None, *, confirm=1, lagged=False):
    if threshold is None:
        return candidate_definition(ORIGINAL_PROFILES[0])
    field = "roc5_lag1" if lagged else "roc5"
    profile = VxnGateProfile(
        threshold_name(threshold),
        target_transition="BULL->CAUTION",
        entry_condition=f"VXN.{field} >= {threshold:g}",
    )
    definition = candidate_definition(profile)
    _find_rule(definition, "BULL", "CAUTION")["confirm"] = confirm
    return definition


def _run_definition(
    name,
    definition,
    *,
    commission=COMMISSION,
    slippage=SLIPPAGE,
):
    strategy = DeclarativeStrategy(definition)
    backtest = VxnStateBacktest(
        strategy,
        tickers=strategy.required_tickers,
        commission=commission,
        slippage=slippage,
    )
    history, trades, rebalances = backtest.run_all()
    attribution = RetirementAllocationAttribution(
        history,
        backtest.data,
        rebalances,
    )
    return {
        "name": name,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "market_data": backtest.data,
        "events": attribution.transition_events(),
        "quality": attribution.transition_quality(),
    }


def _metrics(history, start=None, end=None):
    sample = history.loc[start:end]
    if len(sample) < 2:
        return None
    summary = Performance(sample).summary()
    return {
        "CAGR": summary["CAGR"],
        "MDD": summary["MDD"],
        "Sharpe": summary["Sharpe"],
        "Calmar": summary["Calmar"],
    }


def _transition_row(result):
    quality = result["quality"]
    row = quality.loc[quality["Transition"] == "BULL->CAUTION"]
    return row.iloc[0] if not row.empty else pd.Series(dtype=float)


def threshold_surface(results):
    baseline = results["BASELINE"]
    baseline_full = _metrics(baseline["history"])
    baseline_dev = _metrics(baseline["history"], end="2020-12-31")
    baseline_recent = _metrics(
        baseline["history"], start="2021-01-01"
    )
    baseline_transition = _transition_row(baseline)
    rows = []
    for threshold in THRESHOLDS:
        name = threshold_name(threshold)
        result = results[name]
        full = _metrics(result["history"])
        development = _metrics(result["history"], end="2020-12-31")
        recent = _metrics(result["history"], start="2021-01-01")
        transition = _transition_row(result)
        rows.append({
            "Profile": name,
            "Threshold": threshold,
            "CAGRGap": full["CAGR"] - baseline_full["CAGR"],
            "MDDImprovement": full["MDD"] - baseline_full["MDD"],
            "CalmarGap": full["Calmar"] - baseline_full["Calmar"],
            "DevelopmentCAGRGap": (
                development["CAGR"] - baseline_dev["CAGR"]
            ),
            "RecentCAGRGap": recent["CAGR"] - baseline_recent["CAGR"],
            "RecentMDDImprovement": (
                recent["MDD"] - baseline_recent["MDD"]
            ),
            "TransitionCount": transition.get("Count"),
            "BaselineTransitionCount": baseline_transition.get("Count"),
            "DirectionalSuccessRate20D": transition.get(
                "DirectionalSuccessRate20D"
            ),
            "BaselineDirectionalSuccessRate20D": baseline_transition.get(
                "DirectionalSuccessRate20D"
            ),
            "AvgQQQForwardMaxDrawdown20D": transition.get(
                "AvgQQQForwardMaxDrawdown20D"
            ),
            "BaselineAvgQQQForwardMaxDrawdown20D": baseline_transition.get(
                "AvgQQQForwardMaxDrawdown20D"
            ),
        })
    return pd.DataFrame(rows)


def walk_forward_selection(results):
    rows = []
    for fold, train_end, test_start, test_end in FOLDS:
        train_candidates = []
        for name, result in results.items():
            metrics = _metrics(result["history"], end=train_end)
            if metrics is not None:
                train_candidates.append((metrics["Calmar"], name, metrics))
        _, selected, train_metrics = max(train_candidates)
        selected_test = _metrics(
            results[selected]["history"], test_start, test_end
        )
        baseline_test = _metrics(
            results["BASELINE"]["history"], test_start, test_end
        )
        if selected_test is None or baseline_test is None:
            continue
        rows.append({
            "Fold": fold,
            "TrainEnd": train_end,
            "TestStart": test_start,
            "TestEnd": test_end,
            "SelectedProfile": selected,
            "SelectedThreshold": next(
                (
                    threshold
                    for threshold in THRESHOLDS
                    if threshold_name(threshold) == selected
                ),
                np.nan,
            ),
            "TrainCalmar": train_metrics["Calmar"],
            "TestCAGRGap": selected_test["CAGR"] - baseline_test["CAGR"],
            "TestMDDImprovement": (
                selected_test["MDD"] - baseline_test["MDD"]
            ),
            "TestCalmarGap": (
                selected_test["Calmar"] - baseline_test["Calmar"]
            ),
        })
    return pd.DataFrame(rows)


def discover_drawdown_episodes(prices, threshold=-0.10):
    """Find peak-to-recovery episodes that cross the chosen drawdown level."""
    clean = prices.dropna().astype(float)
    if clean.empty:
        return pd.DataFrame()
    peak_value = clean.iloc[0]
    peak_date = clean.index[0]
    active = None
    rows = []
    for date, price in clean.iloc[1:].items():
        if active is None:
            if price >= peak_value:
                peak_value = price
                peak_date = date
            elif price / peak_value - 1.0 <= threshold:
                active = {
                    "PeakDate": peak_date,
                    "PeakValue": peak_value,
                    "TroughDate": date,
                    "TroughValue": price,
                }
        else:
            if price < active["TroughValue"]:
                active["TroughDate"] = date
                active["TroughValue"] = price
            if price >= active["PeakValue"]:
                rows.append({
                    **active,
                    "EndDate": date,
                    "Recovered": True,
                })
                peak_value = price
                peak_date = date
                active = None
    if active is not None:
        rows.append({
            **active,
            "EndDate": clean.index[-1],
            "Recovered": False,
        })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result.insert(0, "Episode", [f"DD10_{i + 1}" for i in range(len(result))])
    result["MaxDrawdown"] = (
        result["TroughValue"] / result["PeakValue"] - 1.0
    )
    return result


def _annualized_return(returns):
    clean = returns.dropna()
    if clean.empty:
        return np.nan
    return float(np.prod(1.0 + clean) ** (252.0 / len(clean)) - 1.0)


def _mdd_from_returns(returns):
    wealth = (1.0 + returns.dropna()).cumprod()
    if wealth.empty:
        return np.nan
    return float((wealth / wealth.cummax() - 1.0).min())


def episode_dependency(baseline, candidate):
    index = baseline["history"].index.intersection(candidate["history"].index)
    baseline_returns = baseline["history"].loc[index, "Portfolio"].pct_change()
    candidate_returns = candidate["history"].loc[index, "Portfolio"].pct_change()
    qqq = baseline["market_data"].loc[index, "QQQ_Close"]
    episodes = discover_drawdown_episodes(qqq)
    excess_log = np.log1p(candidate_returns) - np.log1p(baseline_returns)
    total_excess_log = excess_log.sum()
    episode_rows = []
    leave_one_out_rows = []
    for _, episode in episodes.iterrows():
        mask = (index > episode["PeakDate"]) & (index <= episode["EndDate"])
        contribution = excess_log.loc[mask].sum()
        episode_rows.append({
            **episode.to_dict(),
            "ExcessLogReturnContribution": contribution,
            "ShareOfTotalExcessLogReturn": (
                contribution / total_excess_log
                if not np.isclose(total_excess_log, 0.0)
                else np.nan
            ),
        })
        kept = ~mask
        base_kept = baseline_returns.loc[kept]
        candidate_kept = candidate_returns.loc[kept]
        leave_one_out_rows.append({
            "ExcludedEpisode": episode["Episode"],
            "ExcludedPeakDate": episode["PeakDate"],
            "ExcludedEndDate": episode["EndDate"],
            "CAGRGapWithoutEpisode": (
                _annualized_return(candidate_kept)
                - _annualized_return(base_kept)
            ),
            "MDDImprovementWithoutEpisode": (
                _mdd_from_returns(candidate_kept)
                - _mdd_from_returns(base_kept)
            ),
        })
    return pd.DataFrame(episode_rows), pd.DataFrame(leave_one_out_rows)


def paired_block_bootstrap(
    baseline_returns,
    candidate_returns,
    *,
    samples=2000,
    block_length=21,
    seed=20260825,
):
    """Bootstrap paired monthly blocks while preserving within-block order."""
    paired = pd.concat(
        [
            baseline_returns.rename("Baseline"),
            candidate_returns.rename("Candidate"),
        ],
        axis=1,
    ).dropna()
    values = paired.to_numpy(dtype=float)
    count = len(values)
    if count < block_length:
        raise ValueError("Not enough paired returns for block bootstrap")
    rng = np.random.default_rng(seed)
    cagr_gaps = np.empty(samples)
    mdd_improvements = np.empty(samples)
    blocks_needed = int(np.ceil(count / block_length))
    offsets = np.arange(block_length)
    for sample in range(samples):
        starts = rng.integers(0, count, size=blocks_needed)
        indices = (starts[:, None] + offsets[None, :]) % count
        draw = values[indices.ravel()[:count]]
        baseline = pd.Series(draw[:, 0])
        candidate = pd.Series(draw[:, 1])
        cagr_gaps[sample] = (
            _annualized_return(candidate) - _annualized_return(baseline)
        )
        mdd_improvements[sample] = (
            _mdd_from_returns(candidate) - _mdd_from_returns(baseline)
        )
    return {
        "Samples": samples,
        "BlockLength": block_length,
        "CAGRGapMean": cagr_gaps.mean(),
        "CAGRGapLower95": np.quantile(cagr_gaps, 0.025),
        "CAGRGapUpper95": np.quantile(cagr_gaps, 0.975),
        "ProbabilityCAGRGapPositive": np.mean(cagr_gaps > 0),
        "MDDImprovementMean": mdd_improvements.mean(),
        "MDDImprovementLower95": np.quantile(
            mdd_improvements, 0.025
        ),
        "MDDImprovementUpper95": np.quantile(
            mdd_improvements, 0.975
        ),
        "ProbabilityMDDImprovementPositive": np.mean(
            mdd_improvements > 0
        ),
    }


def block_max_reality_check(
    excess_log_returns,
    *,
    samples=2000,
    block_length=21,
    seed=20260825,
):
    """Correct threshold search by bootstrapping the best centered strategy."""
    clean = excess_log_returns.dropna()
    values = clean.to_numpy(dtype=float)
    count = len(values)
    if count < block_length:
        raise ValueError("Not enough excess returns for reality check")
    observed_by_profile = values.mean(axis=0) * 252.0
    best_index = int(np.argmax(observed_by_profile))
    observed_max = observed_by_profile[best_index]
    centered = values - values.mean(axis=0, keepdims=True)
    rng = np.random.default_rng(seed)
    blocks_needed = int(np.ceil(count / block_length))
    offsets = np.arange(block_length)
    bootstrap_maxima = np.empty(samples)
    for sample in range(samples):
        starts = rng.integers(0, count, size=blocks_needed)
        indices = (starts[:, None] + offsets[None, :]) % count
        draw = centered[indices.ravel()[:count]]
        bootstrap_maxima[sample] = np.max(draw.mean(axis=0) * 252.0)
    return {
        "Samples": samples,
        "BlockLength": block_length,
        "CandidateCount": clean.shape[1],
        "BestProfile": clean.columns[best_index],
        "ObservedBestAnnualizedLogExcess": observed_max,
        "RealityCheckPValue": (
            (1.0 + np.sum(bootstrap_maxima >= observed_max))
            / (samples + 1.0)
        ),
        "BootstrapMaxLower95": np.quantile(bootstrap_maxima, 0.025),
        "BootstrapMaxUpper95": np.quantile(bootstrap_maxima, 0.975),
    }


@dataclass
class LogisticModel:
    coefficients: np.ndarray
    mean: np.ndarray
    scale: np.ndarray

    def predict(self, values):
        standardized = (values - self.mean) / self.scale
        design = np.column_stack([np.ones(len(standardized)), standardized])
        score = np.clip(design @ self.coefficients, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-score))


def fit_logistic(values, labels, l2=1.0, max_iter=100):
    """Fit a small ridge-logistic model using only NumPy."""
    values = np.asarray(values, dtype=float)
    labels = np.asarray(labels, dtype=float)
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale[scale < 1e-12] = 1.0
    standardized = (values - mean) / scale
    design = np.column_stack([np.ones(len(standardized)), standardized])
    coefficients = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * l2
    penalty[0, 0] = 0.0
    for _ in range(max_iter):
        score = np.clip(design @ coefficients, -35.0, 35.0)
        probability = 1.0 / (1.0 + np.exp(-score))
        weight = probability * (1.0 - probability)
        gradient = design.T @ (probability - labels) + penalty @ coefficients
        hessian = design.T @ (weight[:, None] * design) + penalty
        step = np.linalg.solve(hessian, gradient)
        coefficients -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return LogisticModel(coefficients, mean, scale)


def _log_loss(labels, probabilities):
    probabilities = np.clip(probabilities, 1e-9, 1.0 - 1e-9)
    return float(
        -np.mean(
            labels * np.log(probabilities)
            + (1.0 - labels) * np.log(1.0 - probabilities)
        )
    )


def predictive_opportunities(baseline):
    frame = baseline["history"].join(
        baseline["market_data"], how="inner"
    ).copy()
    frame["QQQForwardReturn20D"] = (
        frame["QQQ_Close"].shift(-20) / frame["QQQ_Close"] - 1.0
    )
    path = RetirementAllocationAttribution._forward_path_metrics(
        frame["QQQ_Close"], 20
    )
    frame["QQQForwardMaxDrawdown20D"] = path["ForwardMaxDrawdown"]
    frame["QQQDown20D"] = (
        frame["QQQForwardReturn20D"] < 0
    ).astype(float)
    frame["QQQDrawdown5Pct20D"] = (
        frame["QQQForwardMaxDrawdown20D"] <= -0.05
    ).astype(float)
    core = (
        frame["StrategyState"].eq("BULL")
        & (frame["QQQ_Close"] < frame["QQQ_EMA20"])
        & (frame["RiskOffScore"] >= 3)
    )
    onset = core & ~core.shift(fill_value=False)
    opportunities = frame.loc[onset].copy()
    opportunities = opportunities.dropna(
        subset=[
            *BASE_FEATURES,
            "VXN_ROC5",
            "QQQForwardReturn20D",
            "QQQForwardMaxDrawdown20D",
        ]
    )
    return frame, opportunities


def walk_forward_predictive(opportunities, label, added_feature):
    rows = []
    all_labels = []
    all_base = []
    all_augmented = []
    for fold, _, test_start, test_end in FOLDS:
        test_start_date = pd.Timestamp(test_start)
        train_cutoff = test_start_date - pd.Timedelta(days=30)
        train = opportunities.loc[opportunities.index <= train_cutoff]
        test = opportunities.loc[test_start:test_end]
        columns = [*BASE_FEATURES, added_feature, label]
        train = train.dropna(subset=columns)
        test = test.dropna(subset=columns)
        if (
            len(train) < 12
            or len(test) < 2
            or train[label].nunique() < 2
        ):
            continue
        base_model = fit_logistic(train[list(BASE_FEATURES)], train[label])
        augmented_features = [*BASE_FEATURES, added_feature]
        augmented_model = fit_logistic(
            train[augmented_features], train[label]
        )
        labels = test[label].to_numpy(dtype=float)
        base_probability = base_model.predict(test[list(BASE_FEATURES)])
        augmented_probability = augmented_model.predict(
            test[augmented_features]
        )
        rows.append({
            "Label": label,
            "AddedFeature": added_feature,
            "Fold": fold,
            "TrainCount": len(train),
            "TestCount": len(test),
            "PositiveRate": labels.mean(),
            "BaseBrier": np.mean((base_probability - labels) ** 2),
            "AugmentedBrier": np.mean(
                (augmented_probability - labels) ** 2
            ),
            "BrierImprovement": (
                np.mean((base_probability - labels) ** 2)
                - np.mean((augmented_probability - labels) ** 2)
            ),
            "BaseLogLoss": _log_loss(labels, base_probability),
            "AugmentedLogLoss": _log_loss(labels, augmented_probability),
            "LogLossImprovement": (
                _log_loss(labels, base_probability)
                - _log_loss(labels, augmented_probability)
            ),
            "StandardizedAddedCoefficient": (
                augmented_model.coefficients[-1]
            ),
        })
        all_labels.extend(labels)
        all_base.extend(base_probability)
        all_augmented.extend(augmented_probability)
    fold_report = pd.DataFrame(rows)
    if not all_labels:
        return fold_report, None
    labels = np.asarray(all_labels)
    base = np.asarray(all_base)
    augmented = np.asarray(all_augmented)
    summary = {
        "Label": label,
        "AddedFeature": added_feature,
        "TestCount": len(labels),
        "BaseBrier": np.mean((base - labels) ** 2),
        "AugmentedBrier": np.mean((augmented - labels) ** 2),
        "BrierImprovement": (
            np.mean((base - labels) ** 2)
            - np.mean((augmented - labels) ** 2)
        ),
        "BaseLogLoss": _log_loss(labels, base),
        "AugmentedLogLoss": _log_loss(labels, augmented),
        "LogLossImprovement": (
            _log_loss(labels, base) - _log_loss(labels, augmented)
        ),
        "PositiveBrierFolds": int((fold_report["BrierImprovement"] > 0).sum()),
        "FoldCount": len(fold_report),
        "MeanStandardizedAddedCoefficient": fold_report[
            "StandardizedAddedCoefficient"
        ].mean(),
    }
    return fold_report, summary


def predictive_and_placebo(baseline):
    daily, opportunities = predictive_opportunities(baseline)
    fold_frames = []
    summary_rows = []
    placebo_rows = []
    complete_vxn = daily["VXN_ROC5"].to_numpy(copy=True)
    offsets = np.unique(
        np.linspace(20, len(daily) - 20, 49, dtype=int)
    )
    for label in ("QQQDown20D", "QQQDrawdown5Pct20D"):
        folds, summary = walk_forward_predictive(
            opportunities, label, "VXN_ROC5"
        )
        if summary is None:
            continue
        fold_frames.append(folds)
        actual_improvement = summary["BrierImprovement"]
        placebo_improvements = []
        for number, offset in enumerate(offsets, start=1):
            column = f"VXN_PLACEBO_{number}"
            shifted = pd.Series(
                np.roll(complete_vxn, offset),
                index=daily.index,
            )
            placebo_opportunities = opportunities.assign(
                **{column: shifted.loc[opportunities.index]}
            )
            _, placebo_summary = walk_forward_predictive(
                placebo_opportunities, label, column
            )
            if placebo_summary is None:
                continue
            improvement = placebo_summary["BrierImprovement"]
            placebo_improvements.append(improvement)
            placebo_rows.append({
                "Label": label,
                "Offset": offset,
                "BrierImprovement": improvement,
            })
        placebo_values = np.asarray(placebo_improvements)
        summary["PlaceboCount"] = len(placebo_values)
        summary["PlaceboPercentile"] = (
            100.0 * np.mean(actual_improvement > placebo_values)
        )
        summary["PlaceboPValue"] = (
            (1.0 + np.sum(placebo_values >= actual_improvement))
            / (len(placebo_values) + 1.0)
        )
        summary_rows.append(summary)
    return (
        pd.concat(fold_frames, ignore_index=True),
        pd.DataFrame(summary_rows),
        pd.DataFrame(placebo_rows),
        opportunities,
    )


def stress_report(standard_results):
    baseline = standard_results["BASELINE"]
    threshold5 = standard_results[threshold_name(5.0)]
    scenarios = {
        "STANDARD": (baseline, threshold5),
        "VXN_SIGNAL_LAG1": (
            baseline,
            _run_definition(
                "VXN_SIGNAL_LAG1",
                threshold_definition(5.0, lagged=True),
            ),
        ),
        "CONFIRM_2_DAYS": (
            baseline,
            _run_definition(
                "CONFIRM_2_DAYS",
                threshold_definition(5.0, confirm=2),
            ),
        ),
        "CONFIRM_3_DAYS": (
            baseline,
            _run_definition(
                "CONFIRM_3_DAYS",
                threshold_definition(5.0, confirm=3),
            ),
        ),
        "COSTS_2X": (
            _run_definition(
                "BASELINE_COSTS_2X",
                threshold_definition(),
                commission=COMMISSION * 2,
                slippage=SLIPPAGE * 2,
            ),
            _run_definition(
                "VXN_COSTS_2X",
                threshold_definition(5.0),
                commission=COMMISSION * 2,
                slippage=SLIPPAGE * 2,
            ),
        ),
    }
    rows = []
    for scenario, (reference, candidate) in scenarios.items():
        base_full = _metrics(reference["history"])
        candidate_full = _metrics(candidate["history"])
        base_recent = _metrics(reference["history"], start="2021-01-01")
        candidate_recent = _metrics(
            candidate["history"], start="2021-01-01"
        )
        rows.append({
            "Scenario": scenario,
            "CAGRGap": candidate_full["CAGR"] - base_full["CAGR"],
            "MDDImprovement": (
                candidate_full["MDD"] - base_full["MDD"]
            ),
            "RecentCAGRGap": (
                candidate_recent["CAGR"] - base_recent["CAGR"]
            ),
            "RecentMDDImprovement": (
                candidate_recent["MDD"] - base_recent["MDD"]
            ),
        })
    return pd.DataFrame(rows)


def run_vxn_robustness():
    download_vxn()
    results = {
        "BASELINE": _run_definition("BASELINE", threshold_definition())
    }
    for threshold in THRESHOLDS:
        name = threshold_name(threshold)
        results[name] = _run_definition(
            name,
            threshold_definition(threshold),
        )

    surface = threshold_surface(results)
    walkforward = walk_forward_selection(results)
    episodes, leave_one_out = episode_dependency(
        results["BASELINE"], results[threshold_name(5.0)]
    )
    common_index = results["BASELINE"]["history"].index.intersection(
        results[threshold_name(5.0)]["history"].index
    )
    bootstrap = pd.DataFrame([paired_block_bootstrap(
        results["BASELINE"]["history"].loc[
            common_index, "Portfolio"
        ].pct_change(),
        results[threshold_name(5.0)]["history"].loc[
            common_index, "Portfolio"
        ].pct_change(),
    )])
    baseline_returns = results["BASELINE"]["history"].loc[
        common_index, "Portfolio"
    ].pct_change()
    excess_columns = {}
    for threshold in THRESHOLDS:
        name = threshold_name(threshold)
        candidate_returns = results[name]["history"].loc[
            common_index, "Portfolio"
        ].pct_change()
        excess_columns[name] = (
            np.log1p(candidate_returns) - np.log1p(baseline_returns)
        )
    reality_check = pd.DataFrame([block_max_reality_check(
        pd.DataFrame(excess_columns),
    )])
    predictive_folds, predictive_summary, placebo, opportunities = (
        predictive_and_placebo(results["BASELINE"])
    )
    stress = stress_report(results)
    opportunity_report = opportunities.reset_index().rename(
        columns={"index": "Date"}
    )[[
        "Date",
        "RiskOffScore",
        "QQQ_ROC5",
        "QQQ_ROC20",
        "SPY_ROC5",
        "VXN_ROC5",
        "QQQForwardReturn20D",
        "QQQForwardMaxDrawdown20D",
        "QQQDown20D",
        "QQQDrawdown5Pct20D",
    ]]
    reports = {
        "vxn_robustness_threshold_surface": surface,
        "vxn_robustness_walkforward": walkforward,
        "vxn_robustness_episodes": episodes,
        "vxn_robustness_episode_leave_one_out": leave_one_out,
        "vxn_robustness_block_bootstrap": bootstrap,
        "vxn_robustness_reality_check": reality_check,
        "vxn_robustness_predictive_folds": predictive_folds,
        "vxn_robustness_predictive_summary": predictive_summary,
        "vxn_robustness_placebo": placebo,
        "vxn_robustness_opportunities": opportunity_report,
        "vxn_robustness_stress": stress,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    robustness_reports = run_vxn_robustness()
    for name in (
        "vxn_robustness_threshold_surface",
        "vxn_robustness_walkforward",
        "vxn_robustness_predictive_summary",
        "vxn_robustness_stress",
    ):
        print(f"\n{name}")
        print(robustness_reports[name].to_string(index=False))
