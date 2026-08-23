"""Compare lower TDF vintages with adding BND to the safe sleeve.

TDF2030/2040 research proxies use the same global-equity split, monthly
rebalancing, and one-session observation lag as the validated TDF2050 proxy.
The equity allocations (50.32%, 66.68%, 75.82%) come from one KODEX
allocation snapshot, so this is a vintage comparison rather than a glide-path
reconstruction.
"""

from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import pandas as pd

from backtest import Backtest
from config import DATA_DIR
from indicators import Indicator
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from tdf_proxy import build_proxy_frame
from validation.tdf_bil_retirement_insight import definition_for


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
BASE_CONFIG = {
    "canonical_qqq": 0.70,
    "recovery_qqq": 0.50,
    "upper_qqq": 0.775,
    "effective_cap": False,
}
EQUITY_SPLIT = {"SPY": 0.55, "VXUS": 0.45}
VINTAGE_EQUITY = {
    "TDF2030_PROXY": 0.5032,
    "TDF2040_PROXY": 0.6668,
    "TDF2050_PROXY": 0.7582,
}
TDF_ONLY_SHARES = (0.40, 0.60, 0.80, 1.00)
BND_MIXES = (
    (0.20, 0.20, 0.60),
    (0.20, 0.40, 0.40),
    (0.20, 0.60, 0.20),
    (0.40, 0.20, 0.40),
    (0.40, 0.40, 0.20),
    (0.40, 0.60, 0.00),
    (0.60, 0.20, 0.20),
    (0.60, 0.40, 0.00),
    (0.80, 0.00, 0.20),
    (0.80, 0.20, 0.00),
)


def proxy_weights(equity_weight):
    return {
        "SPY": equity_weight * EQUITY_SPLIT["SPY"],
        "VXUS": equity_weight * EQUITY_SPLIT["VXUS"],
        "BND": 1 - equity_weight,
    }


def prepare_data(output_dir):
    output_dir = Path(output_dir)
    for ticker in ("QQQ", "BND", "BIL", "VXUS"):
        copy2(DATA_DIR / f"{ticker}.csv", output_dir / f"{ticker}.csv")
    components = {
        ticker: pd.read_csv(
            DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True
        )
        for ticker in ("SPY", "VXUS", "BND")
    }
    for ticker, equity_weight in VINTAGE_EQUITY.items():
        frame = Indicator.add_indicators(
            build_proxy_frame(components, weights=proxy_weights(equity_weight))
        )
        frame.to_csv(output_dir / f"{ticker}.csv")


def fixed_vintage_definition(ticker, tdf_share):
    definition = definition_for(BASE_CONFIG)
    definition["assets"]["required"] = ["QQQ", ticker, "BIL"]
    definition["state"]["safe_tdf_share"] = {
        "initial": f"{tdf_share * 100}%",
        "rules": [],
    }
    definition["rebalance"] = [
        rule for rule in definition["rebalance"]
        if "changed(state.safe_tdf_share)" not in rule["when"]
    ]
    if ticker != "TDF2050_PROXY":
        for rule in definition["target"]:
            rule["weights"][ticker] = rule["weights"].pop("TDF2050_PROXY")
    return definition


def _safe_weights(qqq, tdf_share, bnd_share):
    tdf = f"round((1 - ({qqq})) * {tdf_share}, 10)"
    bnd = f"round((1 - ({qqq})) * {bnd_share}, 10)"
    return {
        "QQQ": qqq,
        "TDF2050_PROXY": tdf,
        "BND": bnd,
        "BIL": f"round(max(0, 1 - ({qqq}) - ({tdf}) - ({bnd})), 10)",
    }


def bnd_mix_definition(tdf_share, bnd_share, bil_share):
    if abs(tdf_share + bnd_share + bil_share - 1) > 1e-8:
        raise ValueError("safe-sleeve weights must sum to 100%")
    definition = definition_for(BASE_CONFIG)
    definition["assets"]["required"] = [
        "QQQ", "TDF2050_PROXY", "BND", "BIL"
    ]
    definition["state"]["safe_tdf_share"] = {"initial": "0%", "rules": []}
    band_condition = definition["target"][0]["when"]
    definition["target"] = [
        {
            "when": band_condition,
            "weights": _safe_weights(
                "portfolio.weight.QQQ", tdf_share, bnd_share
            ),
        },
        {
            "weights": _safe_weights(
                "state.risk_weight", tdf_share, bnd_share
            ),
        },
    ]
    definition["rebalance"] = [
        rule for rule in definition["rebalance"]
        if "changed(state.safe_tdf_share)" not in rule["when"]
    ]
    return definition


def run(definition, data_dir):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, data_dir=Path(data_dir), tickers=strategy.required_tickers
    ).run_all()
    return history, len(trades), len(rebalances)


def period_metrics(history, start, end):
    sample = history.loc[start:end]
    performance = Performance(sample)
    return performance.cagr(), performance.mdd(), performance.calmar_ratio()


def main():
    with TemporaryDirectory(prefix="tdf-vintage-") as temporary:
        prepare_data(temporary)
        candidates = {}
        for ticker in VINTAGE_EQUITY:
            for share in TDF_ONLY_SHARES:
                name = f"{ticker.removesuffix('_PROXY')} {share:.0%} / BIL {1-share:.0%}"
                candidates[name] = run(
                    fixed_vintage_definition(ticker, share), temporary
                )
        for tdf_share, bnd_share, bil_share in BND_MIXES:
            name = (
                f"TDF2050 {tdf_share:.0%} / BND {bnd_share:.0%} / "
                f"BIL {bil_share:.0%}"
            )
            candidates[name] = run(
                bnd_mix_definition(tdf_share, bnd_share, bil_share), temporary
            )
        benchmark = run(
            load_strategy_definition(
                ROOT / "strategies" / "07_band_7030_bnd.yaml"
            ),
            temporary,
        )[0]

    common_start = pd.Timestamp("2011-03-29")
    common_end = min(history.index.max() for history, _, _ in candidates.values())
    benchmark_cagr, benchmark_mdd, _ = period_metrics(
        benchmark, common_start, common_end
    )
    rows, rolling_rows = [], []
    for name, (history, trades, rebalances) in candidates.items():
        cagr, mdd, calmar = period_metrics(history, common_start, common_end)
        later_cagr, later_mdd, _ = period_metrics(
            history, "2021-01-01", common_end
        )
        for year in range(2012, 2024):
            start = pd.Timestamp(year, 1, 1)
            end = pd.Timestamp(year + 2, 12, 31)
            candidate_cagr, candidate_mdd, _ = period_metrics(history, start, end)
            window_cagr, window_mdd, _ = period_metrics(benchmark, start, end)
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                "CAGRGap": candidate_cagr - window_cagr,
                "MDDImprovement": candidate_mdd - window_mdd,
            })
        costs = (
            history.loc[common_start:common_end, "TransactionCosts"].iloc[-1]
            - history.loc[common_start:common_end, "TransactionCosts"].iloc[0]
        )
        rows.append({
            "Candidate": name,
            "CAGR": cagr,
            "MDD": mdd,
            "Calmar": calmar,
            "CAGRGap": cagr - benchmark_cagr,
            "MDDImprovement": mdd - benchmark_mdd,
            "LaterCAGR": later_cagr,
            "LaterMDD": later_mdd,
            "TransactionCosts": costs,
            "Trades": trades,
            "Rebalances": rebalances,
        })

    rolling = pd.DataFrame(rolling_rows)
    robustness = []
    for name, group in rolling.groupby("Candidate"):
        both = (group["CAGRGap"] > 0) & (group["MDDImprovement"] > 0)
        robustness.append({
            "Candidate": name,
            "CAGRWins": int((group["CAGRGap"] > 0).sum()),
            "MDDWins": int((group["MDDImprovement"] > 0).sum()),
            "BothWins": int(both.sum()),
            "WorstCAGRGap": group["CAGRGap"].min(),
            "WorstMDDImprovement": group["MDDImprovement"].min(),
        })
    summary = pd.DataFrame(rows).merge(
        pd.DataFrame(robustness), on="Candidate"
    ).sort_values(["BothWins", "Calmar", "CAGR"], ascending=False)
    RESULT_DIR.mkdir(exist_ok=True)
    summary.to_csv(RESULT_DIR / "tdf_vintage_bnd_comparison.csv", index=False)
    rolling.to_csv(RESULT_DIR / "tdf_vintage_bnd_rolling.csv", index=False)

    display = summary.head(15).copy()
    for column in (
        "CAGR", "MDD", "CAGRGap", "MDDImprovement", "LaterCAGR",
        "LaterMDD", "TransactionCosts", "WorstCAGRGap",
        "WorstMDDImprovement",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
