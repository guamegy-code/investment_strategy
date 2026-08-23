"""Adapt the proven retirement allocation structure to QQQ/TDF2050/BIL.

Unlike a nominal weight grid, this experiment counts the TDF proxy's equity
share toward the existing retirement strategy's 70% total-risk ceiling.  It
also retains the monthly 0/50/100 safe-sleeve momentum blend and 15-point
profit band that were useful in the retirement_xx experiments.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[2]
TDF_SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
VXUS_SOURCE = ROOT / "strategies" / "05_profit_band_vxus_v2.yaml"
TDF_EQUITY_SHARE = 0.742

# Each candidate targets the same 70% maximum effective equity exposure.
# The variants only decide how much of that exposure comes from QQQ initially.
CANDIDATES = {
    "Retirement ratio / band 77.5": {
        "canonical_qqq": 0.70, "recovery_qqq": 0.50,
        "upper_qqq": 0.775, "effective_cap": False,
    },
    "Retirement ratio / band 80": {
        "canonical_qqq": 0.70, "recovery_qqq": 0.50,
        "upper_qqq": 0.80, "effective_cap": False,
    },
    "Retirement ratio / band 85": {
        "canonical_qqq": 0.70, "recovery_qqq": 0.50,
        "upper_qqq": 0.85, "effective_cap": False,
    },
    "Retirement ratio / recovery 40": {
        "canonical_qqq": 0.70, "recovery_qqq": 0.40,
        "upper_qqq": 0.80, "effective_cap": False,
    },
    "Risk-capped light TDF": {
        "canonical_qqq": 0.625, "recovery_qqq": 0.425,
        "upper_qqq": 0.775, "effective_cap": True,
    },
}


def _tdf_weight(qqq_expression, effective_cap):
    if not effective_cap:
        return f"round((1 - ({qqq_expression})) * state.safe_tdf_share, 10)"
    return (
        "round(max(0, min((1 - ({q})) * state.safe_tdf_share, "
        "(parameters.maximum_effective_risk - ({q})) / "
        "parameters.tdf_equity_share)), 10)"
    ).format(q=qqq_expression)


def _weights(qqq_expression, effective_cap):
    tdf = _tdf_weight(qqq_expression, effective_cap)
    return {
        "QQQ": qqq_expression,
        "TDF2050_PROXY": tdf,
        "BIL": f"round(1 - ({qqq_expression}) - ({tdf}), 10)",
    }


def definition_for(config):
    definition = deepcopy(load_strategy_definition(TDF_SOURCE))
    canonical = config["canonical_qqq"]
    recovery = config["recovery_qqq"]
    definition["assets"]["required"] = ["QQQ", "TDF2050_PROXY", "BIL"]
    definition["parameters"].update({
        "canonical_risk_weight": canonical,
        "upper_risk_weight": config["upper_qqq"],
        "maximum_effective_risk": 0.70,
        "tdf_equity_share": TDF_EQUITY_SHARE,
        "safe_blend_outer": 1.5,
    })

    # Same monthly momentum blend used by retirement_xx, now between TDF/BIL.
    definition["state"]["safe_tdf_share"] = {
        "initial": "100%",
        "check": "monthly",
        "rules": [
            {
                "when": (
                    "TDF2050_PROXY.roc40 - BIL.roc40 >= "
                    "parameters.safe_blend_outer"
                ),
                "set": "100%",
            },
            {
                "when": (
                    "TDF2050_PROXY.roc40 - BIL.roc40 > "
                    "-parameters.safe_blend_outer"
                ),
                "set": "50%",
            },
            {"otherwise": True, "set": "0%"},
        ],
    }
    definition["state"]["risk_weight"]["initial"] = f"{canonical * 100}%"
    definition["state"]["risk_weight"]["rules"] = [
        {"when": "state.market_mode == 'BEAR'", "set": "0%"},
        {"when": "state.market_mode == 'RECOVERY'", "set": f"{recovery * 100}%"},
        {
            "when": "state.market_mode in ['BULL', 'CAUTION']",
            "set": f"{canonical * 100}%",
        },
    ]

    definition["target"] = [
        {
            "when": (
                "state.market_mode in ['BULL', 'CAUTION'] and "
                "variables.qqq_inside_profit_band"
            ),
            "weights": _weights("portfolio.weight.QQQ", config["effective_cap"]),
        },
        {"weights": _weights("state.risk_weight", config["effective_cap"])},
    ]
    definition["rebalance"] = [
        {
            "when": (
                "state.market_mode in ['BULL', 'CAUTION'] and "
                "portfolio.weight.QQQ >= parameters.upper_risk_weight"
            ),
            "days": "state.execution_days",
        },
        {
            "when": (
                "changed(state.market_mode) and not ("
                "state.market_mode in ['BULL', 'CAUTION'] and "
                "variables.qqq_inside_profit_band) and not ("
                "previous(state.market_mode) == 'CAUTION' and "
                "state.market_mode == 'BULL')"
            ),
            "days": "state.execution_days",
        },
        {
            "when": (
                "changed(state.market_mode) and not ("
                "state.market_mode in ['BULL', 'CAUTION'] and "
                "variables.qqq_inside_profit_band) and "
                "previous(state.market_mode) == 'CAUTION' and "
                "state.market_mode == 'BULL' and ("
                "changed(state.safe_tdf_share) or target_deviation() >= 5%)"
            ),
            "days": "state.execution_days",
        },
        {"when": "changed(state.safe_tdf_share)", "days": 1},
        {
            "check": "monthly",
            "when": (
                "not (state.market_mode in ['BULL', 'CAUTION'] and "
                "variables.qqq_inside_profit_band) and target_deviation() >= 5%"
            ),
            "days": "state.execution_days",
        },
    ]
    return definition


def run_definition(name, definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return name, history, len(rebalances), len(trades)


def metrics(name, history, rebalances, trades, start, end):
    aligned = history.loc[start:end]
    performance = Performance(aligned)
    return {
        "Candidate": name,
        "Start": start.strftime("%Y-%m-%d"),
        "End": end.strftime("%Y-%m-%d"),
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Volatility": performance.volatility(),
        "Rebalances": rebalances,
        "Trades": trades,
    }


def main():
    runs = [
        run_definition(name, definition_for(config))
        for name, config in CANDIDATES.items()
    ]
    runs.append(run_definition("VXUS2 reference", load_strategy_definition(VXUS_SOURCE)))
    runs.append(run_definition("Current TDF reference", load_strategy_definition(TDF_SOURCE)))
    common_start = max(history.index.min() for _, history, _, _ in runs)
    common_end = min(history.index.max() for _, history, _, _ in runs)
    rows = [
        metrics(name, history, rebalances, trades, common_start, common_end)
        for name, history, rebalances, trades in runs
    ]
    results = pd.DataFrame(rows).sort_values(["Calmar", "CAGR"], ascending=False)
    results.to_csv(ROOT / "results" / "tdf_bil_retirement_insight.csv", index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Volatility"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
