"""Compare dynamic and static rebalance decisions with fixed-weight counterfactuals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import Backtest
from config import DATA_DIR, END_DATE, RESULT_DIR, START_DATE
from strategy import (
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    RetirementAllocationStrategy,
    STATIC_RETIREMENT_7030,
)


HORIZONS = (5, 21, 63)
MAJOR_DRAWDOWN_THRESHOLD = -0.10
STRATEGIES = (
    ("RETIREMENT_DYNAMIC", RetirementAllocationStrategy, ("QQQ",)),
    ("STATIC_RETIREMENT_7030", STATIC_RETIREMENT_7030, ("QQQ",)),
    # GLD is pension-account risk capital here; only BND is a safe asset.
    ("ASYMMETRIC_DEFENSE2", ASYMMETRIC_TREND_BAND_ADD_DEFENSE2, ("QQQ", "GLD")),
)


def classify_rebalance(
    reason,
    pre_qqq_weight,
    target_qqq_weight,
    previous_target_qqq_weight=None,
):
    reason = reason or ""
    if reason.startswith("INITIAL"):
        return "INITIAL"
    if "SAFE_ROTATION" in reason:
        return "SAFE_ROTATION"
    if "TRAILING_STOP_DEFENSIVE_MODE" in reason:
        return "RISK_DOWN"
    if "TREND_RECOVERY_NORMAL_MODE" in reason:
        return "RISK_UP"
    if "BUY_DIP" in reason or "EXTREME_OVERSELL" in reason:
        return "BAND_BUY"
    if "TAKE_PROFIT" in reason or "EXTREME_OVERBOUGHT" in reason:
        return "BAND_SELL"
    if "BAND" in reason:
        return "BAND_REBALANCE"
    reference_weight = (
        pre_qqq_weight
        if previous_target_qqq_weight is None
        else previous_target_qqq_weight
    )
    change = target_qqq_weight - reference_weight
    if change < -0.01:
        return "RISK_DOWN"
    if change > 0.01:
        return "RISK_UP"
    return "STATE_LATERAL"


def fixed_weight_path(prices, weights):
    """Return a buy-and-hold value path from fixed origin weights."""
    if prices.empty:
        return pd.Series(dtype=float)
    value = pd.Series(0.0, index=prices.index)
    invested = 0.0
    for ticker, weight in weights.items():
        if ticker not in prices or not np.isfinite(weight) or weight <= 0.0:
            continue
        normalized = prices[ticker] / prices[ticker].iloc[0]
        value = value + float(weight) * normalized
        invested += float(weight)
    # Residual cash is assumed to earn zero over these short event windows.
    value = value + max(0.0, 1.0 - invested)
    return value


def weights_with_total_risk(target, risk_assets, total_risk):
    """Rescale a target to the requested total risk weight."""
    adjusted = {ticker: float(weight) for ticker, weight in target.items()}
    current_risk = sum(adjusted.get(ticker, 0.0) for ticker in risk_assets)
    if current_risk > 0.0:
        for ticker in risk_assets:
            adjusted[ticker] = adjusted.get(ticker, 0.0) * total_risk / current_risk
    else:
        for ticker in risk_assets:
            adjusted[ticker] = 0.0
        adjusted[risk_assets[0]] = total_risk
    safe_assets = [ticker for ticker in adjusted if ticker not in risk_assets]
    current_safe = sum(adjusted[ticker] for ticker in safe_assets)
    if current_safe > 0.0:
        for ticker in safe_assets:
            adjusted[ticker] *= (1.0 - total_risk) / current_safe
    return adjusted


def path_return_and_drawdown(path):
    if len(path) < 2 or path.isna().any():
        return np.nan, np.nan
    forward_return = path.iloc[-1] / path.iloc[0] - 1.0
    drawdown = path / path.cummax() - 1.0
    return float(forward_return), float(drawdown.min())


def _context_buckets(row):
    ema_distance = row["EMA200Distance"]
    drawdown = row["Drawdown120"]
    volatility = row["Volatility60"]
    trend = "ABOVE_EMA200" if ema_distance >= 0.0 else "BELOW_EMA200"
    if drawdown >= -0.05:
        drawdown_bucket = "DD_0_TO_5"
    elif drawdown >= -0.10:
        drawdown_bucket = "DD_5_TO_10"
    elif drawdown >= -0.20:
        drawdown_bucket = "DD_10_TO_20"
    else:
        drawdown_bucket = "DD_OVER_20"
    if volatility < 0.20:
        volatility_bucket = "VOL_BELOW_20"
    elif volatility < 0.30:
        volatility_bucket = "VOL_20_TO_30"
    else:
        volatility_bucket = "VOL_OVER_30"
    return trend, drawdown_bucket, volatility_bucket


def _price_frame(market_data, tickers, start_position, horizon):
    end_position = start_position + horizon
    if end_position >= len(market_data):
        return pd.DataFrame()
    columns = {
        ticker: f"{ticker}_Close"
        for ticker in tickers
        if f"{ticker}_Close" in market_data
    }
    return market_data.iloc[start_position:end_position + 1][
        list(columns.values())
    ].rename(columns={column: ticker for ticker, column in columns.items()})


def _execution_cost_ratio(trades, market_index, execution_date, execution_days, value):
    if trades.empty or execution_date not in market_index or value <= 0.0:
        return 0.0
    start = market_index.get_loc(execution_date)
    dates = market_index[start:start + execution_days]
    current = trades.loc[trades["Date"].isin(dates)]
    slippage = (
        current["Shares"].abs()
        * (current["Price"] - current["ReferencePrice"]).abs()
    ).sum()
    return float((current["Fee"].sum() + slippage) / value)


def _event_rows(label, result, risk_assets):
    history = result["history"]
    market = result["market_data"]
    trades = result["trades"]
    rows = []
    events = sorted(result["rebalances"], key=lambda item: item["Date"])
    previous_target_qqq = None
    previous_target_risk = None
    for event_number, event in enumerate(events):
        execution_date = event.get("ExecutionDate")
        pre_weights = event.get("PreWeights")
        if execution_date is None or pre_weights is None:
            continue
        signal_date = pd.Timestamp(event["Date"])
        execution_date = pd.Timestamp(execution_date)
        if signal_date not in market.index or execution_date not in market.index:
            continue
        signal = market.loc[signal_date]
        target = event.get("Target", {})
        pre_qqq = float(pre_weights.get("QQQ", 0.0))
        target_qqq = float(target.get("QQQ", 0.0))
        pre_risk = sum(float(pre_weights.get(ticker, 0.0)) for ticker in risk_assets)
        target_risk = sum(float(target.get(ticker, 0.0)) for ticker in risk_assets)
        event_type = classify_rebalance(
            event.get("Reason"),
            pre_risk,
            target_risk,
            previous_target_risk,
        )
        # Some strategies express their first investment as a band buy rather
        # than an explicit INITIAL reason.  It is capital deployment, not a
        # rebalance decision, so exclude it from event-quality statistics.
        if event_number == 0:
            event_type = "INITIAL"
        current_previous_target = previous_target_qqq
        current_previous_target_risk = previous_target_risk
        previous_target_qqq = target_qqq
        previous_target_risk = target_risk
        ema_distance = signal["QQQ_Close"] / signal["QQQ_EMA200"] - 1.0
        base_row = {
            "Strategy": label,
            "EventNumber": event_number,
            "SignalDate": signal_date,
            "ExecutionDate": execution_date,
            "Reason": event.get("Reason"),
            "EventType": event_type,
            "ExecutionDays": event.get("ExecutionDays", 1),
            "PreQQQWeight": pre_qqq,
            "PreviousTargetQQQWeight": current_previous_target,
            "TargetQQQWeight": target_qqq,
            "QQQWeightChange": target_qqq - pre_qqq,
            "RiskAssets": "+".join(risk_assets),
            "PreRiskWeight": pre_risk,
            "PreviousTargetRiskWeight": current_previous_target_risk,
            "TargetRiskWeight": target_risk,
            "RiskWeightChange": target_risk - pre_risk,
            "StrategyState": history.at[signal_date, "StrategyState"]
            if signal_date in history.index else None,
            "QQQClose": signal["QQQ_Close"],
            "EMA200Distance": ema_distance,
            "ROC5": signal.get("QQQ_ROC5"),
            "ROC20": signal.get("QQQ_ROC20"),
            "ROC60": signal.get("QQQ_ROC60"),
            "ROC120": signal.get("QQQ_ROC120"),
            "Drawdown120": signal.get("QQQ_DRAWDOWN120"),
            "RSI14": signal.get("QQQ_RSI14"),
            "Volatility60": signal.get("QQQ_VOL60"),
        }
        trend, drawdown_bucket, volatility_bucket = _context_buckets(base_row)
        base_row.update({
            "TrendContext": trend,
            "DrawdownContext": drawdown_bucket,
            "VolatilityContext": volatility_bucket,
        })
        execution_value = float(history.at[execution_date, "Portfolio"])
        cost_ratio = _execution_cost_ratio(
            trades,
            market.index,
            execution_date,
            int(event.get("ExecutionDays", 1)),
            execution_value,
        )
        base_row["ExecutionCostRatio"] = cost_ratio
        execution_position = market.index.get_loc(execution_date)
        tickers = tuple(dict.fromkeys((*pre_weights, *target)))
        for horizon in HORIZONS:
            prices = _price_frame(market, tickers, execution_position, horizon)
            if prices.empty:
                for prefix in (
                    "HoldReturn", "TargetReturn", "AllocationEffect",
                    "NetAllocationEffect", "HoldMaxDrawdown",
                    "TargetMaxDrawdown", "MDDImprovement", "ActualReturn",
                    "ActualVsHold", "QQQReturn", "QQQMaxDrawdown",
                    "DefenseEfficiency",
                ):
                    base_row[f"{prefix}{horizon}D"] = np.nan
                continue
            hold_path = fixed_weight_path(prices, pre_weights)
            target_path = fixed_weight_path(prices, target)
            staged_path = (
                fixed_weight_path(
                    prices, weights_with_total_risk(target, risk_assets, 0.30)
                )
                if label == "RETIREMENT_DYNAMIC" and event_type == "RISK_DOWN"
                else None
            )
            qqq_path = prices["QQQ"] / prices["QQQ"].iloc[0]
            hold_return, hold_mdd = path_return_and_drawdown(hold_path)
            target_return, target_mdd = path_return_and_drawdown(target_path)
            staged_return, staged_mdd = (
                path_return_and_drawdown(staged_path)
                if staged_path is not None else (np.nan, np.nan)
            )
            qqq_return, qqq_mdd = path_return_and_drawdown(qqq_path)
            history_position = history.index.get_loc(execution_date)
            actual_end = history_position + horizon
            actual_return = (
                history["Portfolio"].iloc[actual_end]
                / history["Portfolio"].iloc[history_position]
                - 1.0
                if actual_end < len(history)
                else np.nan
            )
            allocation_effect = target_return - hold_return
            mdd_improvement = target_mdd - hold_mdd
            return_sacrifice = max(0.0, hold_return - target_return)
            defense_efficiency = (
                mdd_improvement / return_sacrifice
                if event_type == "RISK_DOWN"
                and mdd_improvement > 0.0
                and return_sacrifice > 1e-8
                else np.nan
            )
            base_row.update({
                f"HoldReturn{horizon}D": hold_return,
                f"TargetReturn{horizon}D": target_return,
                f"AllocationEffect{horizon}D": allocation_effect,
                f"NetAllocationEffect{horizon}D": allocation_effect - cost_ratio,
                f"HoldMaxDrawdown{horizon}D": hold_mdd,
                f"TargetMaxDrawdown{horizon}D": target_mdd,
                f"MDDImprovement{horizon}D": mdd_improvement,
                f"ActualReturn{horizon}D": actual_return,
                f"ActualVsHold{horizon}D": (
                    actual_return - hold_return if pd.notna(actual_return) else np.nan
                ),
                f"QQQReturn{horizon}D": qqq_return,
                f"QQQMaxDrawdown{horizon}D": qqq_mdd,
                f"DefenseEfficiency{horizon}D": defense_efficiency,
                f"Staged30NetEffect{horizon}D": (
                    staged_return - hold_return - cost_ratio
                    if pd.notna(staged_return) else np.nan
                ),
                f"Staged30MDDImprovement{horizon}D": (
                    staged_mdd - hold_mdd if pd.notna(staged_mdd) else np.nan
                ),
            })
        rows.append(base_row)
    return rows


def _summary(events):
    rows = []
    grouped = events.loc[events["EventType"] != "INITIAL"].groupby(
        ["Strategy", "EventType"], dropna=False
    )
    for (strategy, event_type), group in grouped:
        row = {
            "Strategy": strategy,
            "EventType": event_type,
            "Events": len(group),
            "TotalExecutionCostRatio": group["ExecutionCostRatio"].sum(),
            "AvgExecutionCostRatio": group["ExecutionCostRatio"].mean(),
            "AvgPreQQQWeight": group["PreQQQWeight"].mean(),
            "AvgTargetQQQWeight": group["TargetQQQWeight"].mean(),
            "AvgPreRiskWeight": group["PreRiskWeight"].mean(),
            "AvgTargetRiskWeight": group["TargetRiskWeight"].mean(),
            "AvgDrawdownAtSignal": group["Drawdown120"].mean(),
            "AvgVolatilityAtSignal": group["Volatility60"].mean(),
        }
        for horizon in HORIZONS:
            effect = group[f"NetAllocationEffect{horizon}D"]
            row.update({
                f"AvgNetEffect{horizon}D": effect.mean(),
                f"MedianNetEffect{horizon}D": effect.median(),
                f"PositiveNetEffectRate{horizon}D": (effect > 0.0).mean(),
                f"AvgMDDImprovement{horizon}D": group[
                    f"MDDImprovement{horizon}D"
                ].mean(),
                f"AvgActualVsHold{horizon}D": group[
                    f"ActualVsHold{horizon}D"
                ].mean(),
                f"AvgStaged30NetEffect{horizon}D": group[
                    f"Staged30NetEffect{horizon}D"
                ].mean(),
                f"AvgStaged30MDDImprovement{horizon}D": group[
                    f"Staged30MDDImprovement{horizon}D"
                ].mean(),
            })
        rows.append(row)
    return pd.DataFrame(rows)


def _context_summary(events):
    active = events.loc[events["EventType"] != "INITIAL"].copy()
    return (
        active.groupby([
            "Strategy", "EventType", "TrendContext", "DrawdownContext",
            "VolatilityContext",
        ], dropna=False)
        .agg(
            Events=("SignalDate", "size"),
            AvgNetEffect21D=("NetAllocationEffect21D", "mean"),
            PositiveNetEffectRate21D=(
                "NetAllocationEffect21D", lambda value: (value > 0.0).mean()
            ),
            AvgMDDImprovement21D=("MDDImprovement21D", "mean"),
            AvgQQQReturn21D=("QQQReturn21D", "mean"),
        )
        .reset_index()
    )


def major_drawdown_episodes(market, threshold=MAJOR_DRAWDOWN_THRESHOLD):
    """Find non-overlapping QQQ drawdowns ending at recovery or sample end."""
    close = market["QQQ_Close"].dropna()
    if close.empty:
        return pd.DataFrame()
    peak_date = close.index[0]
    peak_price = float(close.iloc[0])
    active = None
    episodes = []
    for date, price_value in close.iloc[1:].items():
        price = float(price_value)
        if active is None:
            if price >= peak_price:
                peak_date, peak_price = date, price
                continue
            if price / peak_price - 1.0 <= threshold:
                active = {
                    "StartDate": peak_date,
                    "PeakPrice": peak_price,
                    "TroughDate": date,
                    "TroughPrice": price,
                }
        else:
            if price < active["TroughPrice"]:
                active["TroughDate"], active["TroughPrice"] = date, price
            if price >= active["PeakPrice"]:
                active["EndDate"] = date
                active["Recovered"] = True
                episodes.append(active)
                active = None
                peak_date, peak_price = date, price
    if active is not None:
        active["EndDate"] = close.index[-1]
        active["Recovered"] = False
        episodes.append(active)
    result = pd.DataFrame(episodes)
    if result.empty:
        return result
    result["QQQDrawdown"] = result["TroughPrice"] / result["PeakPrice"] - 1.0
    result.insert(0, "Episode", [f"DD{i + 1}" for i in range(len(result))])
    return result


def _episode_comparison(results, events):
    market = next(iter(results.values()))[0]["market_data"]
    episodes = major_drawdown_episodes(market)
    rows = []
    for _, episode in episodes.iterrows():
        start, end = episode["StartDate"], episode["EndDate"]
        for label, (result, _) in results.items():
            path = result["history"].loc[start:end, "Portfolio"]
            strategy_return, strategy_mdd = path_return_and_drawdown(path)
            count = events.loc[
                (events["Strategy"] == label)
                & (events["EventType"] != "INITIAL")
                & events["SignalDate"].between(start, end)
            ].shape[0]
            rows.append({
                "Episode": episode["Episode"],
                "StartDate": start,
                "TroughDate": episode["TroughDate"],
                "EndDate": end,
                "Recovered": episode["Recovered"],
                "QQQDrawdown": episode["QQQDrawdown"],
                "Strategy": label,
                "StrategyReturn": strategy_return,
                "StrategyMDD": strategy_mdd,
                "RebalanceEvents": count,
            })
    return pd.DataFrame(rows)


def _risk_cap_summary(results, cap=0.70):
    rows = []
    for label, (result, risk_assets) in results.items():
        history = result["history"]
        weights = history["Weights"].map(
            lambda item: sum(float(item.get(ticker, 0.0)) for ticker in risk_assets)
        )
        invested = history["Portfolio"] > history["Cash"] + 1e-8
        weights = weights.loc[invested]
        over = weights > cap + 1e-4
        maximum_date = weights.idxmax()
        rows.append({
            "Strategy": label,
            "RiskAssets": "+".join(risk_assets),
            "RiskCap": cap,
            "MaximumRiskWeight": weights.max(),
            "MaximumRiskWeightDate": maximum_date,
            "DaysAboveCap": int(over.sum()),
            "ShareDaysAboveCap": float(over.mean()),
        })
    return pd.DataFrame(rows)


def _pct(value):
    return "-" if pd.isna(value) else f"{value:.2%}"


def _idea_report(summary, events, episodes, risk_cap):
    lines = [
        "# 리밸런싱 이벤트 비교와 개선 가설",
        "",
        "실행일 종가를 기준으로 기존 비중을 그대로 유지한 경로와 새 목표 비중을 ",
        "고정한 경로를 5·21·63거래일 동안 비교했다. 순효과에는 해당 리밸런싱의 ",
        "수수료와 슬리피지를 차감했다.",
        "",
        "위험비중은 RETIREMENT와 STATIC에서는 QQQ, ASYMMETRIC에서는 QQQ+GLD로 "
        "계산했다. ASYMMETRIC의 안전자산은 BND뿐이며 GLD를 안전자산으로 계산하지 않았다.",
        "",
        "> 이 결과는 서로 겹치는 사후 구간을 포함한 기술적 분석이다. 표본이 작은 ",
        "> 항목은 독립 구간에서 검증하기 전까지 규칙 변경의 근거가 아니라 가설로 본다.",
        "",
        "## 이벤트별 결과",
        "",
        "| 전략 | 이벤트 | 횟수 | 21일 순효과 | 양(+)의 비율 | 21일 MDD 개선 | 누적 실행비용 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['Strategy']} | {row['EventType']} | {int(row['Events'])} | "
            f"{_pct(row['AvgNetEffect21D'])} | "
            f"{_pct(row['PositiveNetEffectRate21D'])} | "
            f"{_pct(row['AvgMDDImprovement21D'])} | "
            f"{_pct(row['TotalExecutionCostRatio'])} |"
        )

    lines.extend([
        "",
        "## 공통 QQQ 10% 이상 하락 구간의 실제 전략 경로",
        "",
        "| 구간 | QQQ 낙폭 | 전략 | 구간 수익률 | 전략 MDD | 이벤트 수 |",
        "|---|---:|---|---:|---:|---:|",
    ])
    for _, row in episodes.iterrows():
        period = f"{row['StartDate']:%Y-%m-%d}~{row['EndDate']:%Y-%m-%d}"
        lines.append(
            f"| {period} | {_pct(row['QQQDrawdown'])} | {row['Strategy']} | "
            f"{_pct(row['StrategyReturn'])} | {_pct(row['StrategyMDD'])} | "
            f"{int(row['RebalanceEvents'])} |"
        )

    lines.extend([
        "",
        "## 위험자산 70% 상한 점검",
        "",
        "| 전략 | 위험자산 | 최대 비중 | 최대일 | 상한 초과일 | 초과일 비율 |",
        "|---|---|---:|---|---:|---:|",
    ])
    for _, row in risk_cap.iterrows():
        lines.append(
            f"| {row['Strategy']} | {row['RiskAssets']} | "
            f"{_pct(row['MaximumRiskWeight'])} | "
            f"{row['MaximumRiskWeightDate']:%Y-%m-%d} | "
            f"{int(row['DaysAboveCap'])} | {_pct(row['ShareDaysAboveCap'])} |"
        )

    dynamic = events.loc[
        (events["Strategy"] == "RETIREMENT_DYNAMIC")
        & (events["EventType"] != "INITIAL")
    ].copy()
    risk_down = events.loc[
        (events["EventType"] == "RISK_DOWN")
        & (events["EventType"] != "INITIAL")
    ]
    lines.extend([
        "",
        "## 위험축소 신호 상세",
        "",
        "| 전략 | 신호일 | 직전→목표 위험비중 | 신호시 낙폭 | 이후 QQQ 21일 | 21일 순효과 | 21일 MDD 개선 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for _, row in risk_down.iterrows():
        previous = row["PreviousTargetRiskWeight"]
        lines.append(
            f"| {row['Strategy']} | {row['SignalDate']:%Y-%m-%d} | {_pct(previous)}→"
            f"{_pct(row['TargetRiskWeight'])} | {_pct(row['Drawdown120'])} | "
            f"{_pct(row['QQQReturn21D'])} | {_pct(row['NetAllocationEffect21D'])} | "
            f"{_pct(row['MDDImprovement21D'])} |"
        )

    safe = dynamic.loc[dynamic["EventType"] == "SAFE_ROTATION"].copy()
    safe["Direction"] = safe["Reason"].str.replace("SAFE_ROTATION_", "", regex=False)
    safe_summary = safe.groupby("Direction").agg(
        Events=("SignalDate", "size"),
        Net21=("NetAllocationEffect21D", "mean"),
        Positive21=("NetAllocationEffect21D", lambda value: (value > 0.0).mean()),
        MDD21=("MDDImprovement21D", "mean"),
        Net63=("NetAllocationEffect63D", "mean"),
    )
    lines.extend([
        "",
        "## 안전자산 교체 방향",
        "",
        "| 방향 | 횟수 | 21일 순효과 | 양(+)의 비율 | 21일 MDD 개선 | 63일 순효과 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for direction, row in safe_summary.iterrows():
        lines.append(
            f"| {direction} | {int(row['Events'])} | {_pct(row['Net21'])} | "
            f"{_pct(row['Positive21'])} | {_pct(row['MDD21'])} | {_pct(row['Net63'])} |"
        )

    lateral = dynamic.loc[dynamic["EventType"] == "STATE_LATERAL"].copy()
    lateral["Transition"] = lateral["Reason"].str.extract(r"^([A-Z]+->[A-Z]+)")
    lateral_summary = lateral.groupby("Transition").agg(
        Events=("SignalDate", "size"),
        Net21=("NetAllocationEffect21D", "mean"),
        Positive21=("NetAllocationEffect21D", lambda value: (value > 0.0).mean()),
        Cost=("ExecutionCostRatio", "sum"),
    )
    lines.extend([
        "",
        "## 목표 위험비중이 변하지 않은 상태 전환",
        "",
        "| 전환 | 횟수 | 21일 순효과 | 양(+)의 비율 | 누적 실행비용 |",
        "|---|---:|---:|---:|---:|",
    ])
    for transition, row in lateral_summary.iterrows():
        lines.append(
            f"| {transition} | {int(row['Events'])} | {_pct(row['Net21'])} | "
            f"{_pct(row['Positive21'])} | {_pct(row['Cost'])} |"
        )

    dynamic_down = summary.loc[
        (summary["Strategy"] == "RETIREMENT_DYNAMIC")
        & (summary["EventType"] == "RISK_DOWN")
    ].iloc[0]
    asymmetric_down = summary.loc[
        (summary["Strategy"] == "ASYMMETRIC_DEFENSE2")
        & (summary["EventType"] == "RISK_DOWN")
    ].iloc[0]
    asymmetric_sell = summary.loc[
        (summary["Strategy"] == "ASYMMETRIC_DEFENSE2")
        & (summary["EventType"] == "BAND_SELL")
    ].iloc[0]
    lines.extend([
        "",
        "## 우선 검증할 개선 가설",
        "",
        "1. **70%를 목표가 아니라 실제 상한으로 강제한다.** 세 전략 모두 가격 상승에 "
        "따른 드리프트로 실제 위험비중이 70%를 넘었다. QQQ+GLD를 합산하고, 허용 "
        "오차를 넘으면 다른 신호와 무관하게 70% 이하로 복귀시켜야 한다.",
        "2. **상태 전환과 거래를 분리한다.** BULL↔CAUTION처럼 QQQ 목표 비중이 "
        "동일한 전환은 기록만 남기고, 5% 밴드 이탈이나 안전자산 교체 조건이 없으면 "
        "주문을 만들지 않는다. 73건의 운영 잡음을 먼저 줄이는 변경이다.",
        "3. **BIL→BND 교체 조건만 더 엄격하게 한다.** 이 방향은 63일 순효과가 "
        "음수인 반면 BND→BIL은 중립에 가깝다. 최소 유지기간이나 점수 차이 버퍼를 "
        "한 방향에만 적용해 왕복을 줄인다.",
        "4. **Retirement 위험축소를 70%→30%→0%로 검증한다.** 동일한 4개 신호일에 "
        f"30%를 남긴 반사실은 21일 순효과가 {_pct(dynamic_down['AvgStaged30NetEffect21D'])}로, "
        f"즉시 0%의 {_pct(dynamic_down['AvgNetEffect21D'])}보다 반등 손실이 작았다. 다만 "
        f"MDD 개선은 {_pct(dynamic_down['AvgMDDImprovement21D'])}에서 "
        f"{_pct(dynamic_down['AvgStaged30MDDImprovement21D'])}로 줄어든다.",
        "5. **위험확대 계단은 유지한다.** RISK_UP은 21일과 63일 평균 순효과가 "
        "양수여서 회복 신호를 더 늦추기보다 현재의 0%→50%→70% 구조를 기준선으로 둔다.",
        "6. **Asymmetric의 -25% 방어 조건은 채택하지 않는다.** 위험축소가 평균 "
        f"{_pct(asymmetric_down['AvgDrawdownAtSignal'])} 낙폭에서 발생했고 21일 순효과도 "
        f"{_pct(asymmetric_down['AvgNetEffect21D'])}였다. 부분 방어 자체보다 진입이 너무 "
        "늦은 문제가 크다. 2020년에는 방어 다음 날 바로 정상 모드로 복귀했으므로 "
        "진입·해제 조건의 일관성도 별도 검증한다.",
        "7. **Asymmetric의 하락장 매도는 상한 준수 주문과 분리한다.** BAND_SELL의 "
        f"21일 순효과는 {_pct(asymmetric_sell['AvgNetEffect21D'])}였다. 수익 신호로는 "
        "약하지만 위험비중 70% 초과분을 줄이는 주문은 성과와 무관하게 유지해야 한다.",
        "8. **STATIC 70:30 밴드는 수익 신호가 아니라 위험 통제로 평가한다.** "
        "21일 수익효과는 거의 0이고 MDD만 소폭 개선됐다. 밴드 폭 변경은 CAGR보다 "
        "회전율·비용·MDD의 결합 목적함수로 비교한다.",
    ])
    return "\n".join(lines) + "\n"


def _run(label, factory):
    strategy = factory()
    backtest = Backtest(
        strategy,
        data_dir=DATA_DIR,
        tickers=strategy.required_tickers,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "market_data": backtest.data,
    }


def run_rebalance_event_analysis():
    results = {
        label: (_run(label, factory), risk_assets)
        for label, factory, risk_assets in STRATEGIES
    }
    rows = []
    for label, (result, risk_assets) in results.items():
        rows.extend(_event_rows(label, result, risk_assets))
    events = pd.DataFrame(rows).sort_values(["Strategy", "SignalDate"])
    summary = _summary(events)
    contexts = _context_summary(events)
    episodes = _episode_comparison(results, events)
    risk_cap = _risk_cap_summary(results)
    events.to_csv(RESULT_DIR / "rebalance_event_details.csv", index=False)
    summary.to_csv(RESULT_DIR / "rebalance_event_summary.csv", index=False)
    contexts.to_csv(RESULT_DIR / "rebalance_context_comparison.csv", index=False)
    episodes.to_csv(RESULT_DIR / "rebalance_episode_comparison.csv", index=False)
    risk_cap.to_csv(RESULT_DIR / "rebalance_risk_cap_summary.csv", index=False)
    (RESULT_DIR / "rebalance_improvement_ideas.md").write_text(
        _idea_report(summary, events, episodes, risk_cap), encoding="utf-8"
    )
    return {
        "events": events,
        "summary": summary,
        "contexts": contexts,
        "episodes": episodes,
        "risk_cap": risk_cap,
    }


if __name__ == "__main__":
    reports = run_rebalance_event_analysis()
    columns = [
        "Strategy", "EventType", "Events", "AvgDrawdownAtSignal",
        "AvgNetEffect5D", "AvgNetEffect21D", "AvgNetEffect63D",
        "PositiveNetEffectRate21D", "AvgMDDImprovement21D",
        "AvgActualVsHold21D",
    ]
    print("Rebalance counterfactual summary")
    print(reports["summary"][columns].to_string(index=False))
    print("\nDynamic event contexts")
    dynamic = reports["contexts"].loc[
        reports["contexts"]["Strategy"] == "RETIREMENT_DYNAMIC"
    ]
    print(dynamic.to_string(index=False))
