"""Compare strategy 29/31 in dot-com and GFC proxy scenarios.

Requires the existing local ``tmp/BAA10Y.csv`` research input. The historical
TDF, BIL, and BND paths are synthetic; this is not an investable product test.
All generated market files live in a temporary directory and are discarded.
"""

from argparse import ArgumentParser
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATA_DIR, EXTENDED_DATA_DIR  # noqa: E402
from strategy30_31_yaml_parity import (  # noqa: E402
    DOTCOM,
    GFC,
    ROOT,
    STRATEGY29,
    STRATEGY31,
    add_credit_observation,
    apply_credit_lag,
    build_data,
    run,
)
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402
from tdf_proxy import build_proxy_frame  # noqa: E402


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, index_col="Date", parse_dates=True)


def compare(periods=("DOTCOM", "GFC"), equity_proxies=("SPY", "QQQ")):
    """Return proxy MDDs using identical market paths for both strategies."""
    credit_path = ROOT / "tmp" / "BAA10Y.csv"
    if not credit_path.is_file():
        raise FileNotFoundError(
            "tmp/BAA10Y.csv is required; do not publish the raw FRED series"
        )

    spy = _read(DATA_DIR / "SPY.csv")
    qqq = _read(EXTENDED_DATA_DIR / "QQQ.csv")
    bnd = _read(EXTENDED_DATA_DIR / "BND.csv")
    equity = {"SPY": spy, "QQQ": qqq}
    windows = {"DOTCOM": DOTCOM, "GFC": GFC}
    results = []

    for period in periods:
        for proxy_ticker in equity_proxies:
            with TemporaryDirectory(prefix="strategy29-31-crisis-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                build_data(directory, dotcom=True)
                apply_credit_lag(directory, 1)
                add_credit_observation(directory)
                tdf = build_proxy_frame(
                    {"SPY": spy, "VXUS": equity[proxy_ticker], "BND": bnd},
                    observation_lag=1,
                )
                tdf.to_csv(directory / "TDF2050_PROXY.csv")

                for number, path in ((29, STRATEGY29), (31, STRATEGY31)):
                    definition = deepcopy(load_strategy_definition(path))
                    # Historical Shiller CAPE is on a different scale from the
                    # current composite valuation score. Use the established
                    # dot-com proxy calibration for both strategies alike.
                    definition["parameters"]["valuation_arm_score"] = 30.0
                    definition["parameters"]["valuation_breakdown_points"] = 4.0
                    metrics, history = run(
                        DeclarativeStrategy(definition), directory, windows[period]
                    )
                    drawdown = history.Portfolio / history.Portfolio.cummax() - 1
                    trough = drawdown.idxmin()
                    peak = history.Portfolio.loc[:trough].idxmax()
                    results.append({
                        "Period": period,
                        "TDF_Equity_Proxy": proxy_ticker,
                        "Strategy": number,
                        "MDD_pct": round(float(metrics["MDD"]) * 100, 3),
                        "Peak": peak.date().isoformat(),
                        "Trough": trough.date().isoformat(),
                        "CAGR_pct": round(float(metrics["CAGR"]) * 100, 3),
                    })
    return pd.DataFrame(results)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=("DOTCOM", "GFC", "BOTH"), default="BOTH")
    parser.add_argument("--tdf-equity-proxy", choices=("SPY", "QQQ", "BOTH"), default="BOTH")
    args = parser.parse_args()
    periods = ("DOTCOM", "GFC") if args.period == "BOTH" else (args.period,)
    proxies = ("SPY", "QQQ") if args.tdf_equity_proxy == "BOTH" else (args.tdf_equity_proxy,)
    print(compare(periods, proxies).to_string(index=False))
