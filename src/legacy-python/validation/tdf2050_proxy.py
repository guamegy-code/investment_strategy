"""Validate the long-history TDF2050 proxy against the listed ETF."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_DIR, RESULT_DIR


ACTUAL_TICKER = "434060.KS"
PROXY_TICKER = "TDF2050_PROXY"
FX_TICKER = "KRW=X"
PERIODS = {
    "CALIBRATION_2022_2024": ("2022-07-01", "2024-12-31"),
    "VALIDATION_2025_PRESENT": ("2025-01-01", None),
    "FULL_OVERLAP": ("2022-07-01", None),
}


def _close(data_dir: Path, ticker: str) -> pd.Series:
    frame = pd.read_csv(data_dir / f"{ticker}.csv", parse_dates=["Date"])
    return frame.set_index("Date")["Close"].astype(float).sort_index()


def proxy_quality(
    actual: pd.Series,
    proxy: pd.Series,
    comparison: str = "PRODUCT_PRICE_KRW",
) -> pd.DataFrame:
    prices = pd.concat({"Actual": actual, "Proxy": proxy}, axis=1, join="inner").dropna()
    rows = []
    for period, (start, end) in PERIODS.items():
        sample = prices.loc[start:end]
        returns = sample.pct_change().dropna()
        difference = returns["Actual"] - returns["Proxy"]
        years = (sample.index[-1] - sample.index[0]).days / 365.25
        rows.append({
            "Comparison": comparison,
            "Period": period,
            "StartDate": sample.index[0].date().isoformat(),
            "EndDate": sample.index[-1].date().isoformat(),
            "Observations": len(sample),
            "Correlation": returns["Actual"].corr(returns["Proxy"]),
            "AnnualizedTrackingError": difference.std() * np.sqrt(252),
            "AnnualizedReturnGap": difference.mean() * 252,
            "ActualCAGR": (sample["Actual"].iloc[-1] / sample["Actual"].iloc[0]) ** (1 / years) - 1,
            "ProxyCAGR": (sample["Proxy"].iloc[-1] / sample["Proxy"].iloc[0]) ** (1 / years) - 1,
            "ActualVolatility": returns["Actual"].std() * np.sqrt(252),
            "ProxyVolatility": returns["Proxy"].std() * np.sqrt(252),
            "Pass": (
                returns["Actual"].corr(returns["Proxy"]) >= 0.60
                and difference.std() * np.sqrt(252) <= 0.12
            ),
        })
    return pd.DataFrame(rows)


def run_validation(data_dir=DATA_DIR, output_dir=RESULT_DIR) -> pd.DataFrame:
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    actual = _close(data_dir, ACTUAL_TICKER)
    proxy = _close(data_dir, PROXY_TICKER)
    fx = _close(data_dir, FX_TICKER).reindex(actual.index).ffill()
    report = pd.concat(
        [
            proxy_quality(actual, proxy, "PRODUCT_PRICE_KRW"),
            proxy_quality(actual / fx, proxy, "STRATEGY_INPUT_FX_REMOVED"),
        ],
        ignore_index=True,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_dir / "tdf2050_proxy_validation.csv", index=False)
    return report


if __name__ == "__main__":
    print(run_validation().to_string(index=False))
