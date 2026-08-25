"""Audit a four-component reconstruction against official CNN F&G history."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from config import DATA_DIR, PROJECT_ROOT, RESULT_DIR
from indicators import Indicator
from .point_in_time_signals import load_point_in_time_signal


CNN_BASE_URL = (
    "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
)
OFFICIAL_START = pd.Timestamp("2021-02-01")
CNN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
SIGNAL_DIR = PROJECT_ROOT / "data_probability_signals"
OFFICIAL_PATH = SIGNAL_DIR / "CNN_FEAR_GREED.csv"
RECONSTRUCTED_PATH = SIGNAL_DIR / "RECONSTRUCTED_FEAR_GREED_4C.csv"
RAW_AUDIT_PATH = RESULT_DIR / "cnn_fear_greed_raw.json"
OFFICIAL_VERSION = "cnn-fear-greed-official-2021-v1"
RECONSTRUCTED_VERSION = "reconstructed-fear-greed-4c-v1"
TRAIN_END = pd.Timestamp("2023-12-31")
HOLDOUT_START = pd.Timestamp("2024-01-01")
PERCENTILE_WINDOW = 756
PERCENTILE_MIN_PERIODS = 252


def parse_official_payload(payload):
    records = payload.get("fear_and_greed_historical", {}).get("data", [])
    if not records:
        raise ValueError("CNN payload has no fear_and_greed_historical data")
    raw = pd.DataFrame(records)
    if not {"x", "y"}.issubset(raw.columns):
        raise ValueError("CNN historical records require x and y fields")
    dates = pd.to_datetime(raw["x"], unit="ms", utc=True).dt.tz_localize(None)
    normalized = pd.DataFrame({
        "ObservationDate": dates.dt.normalize(),
        "AvailableDate": dates.dt.normalize(),
        "Value": pd.to_numeric(raw["y"], errors="raise"),
        "MethodologyVersion": OFFICIAL_VERSION,
        "Source": "CNN",
        "SourceSeries": "Fear & Greed Index",
    })
    normalized = normalized.drop_duplicates(
        "ObservationDate", keep="last"
    ).sort_values("ObservationDate")
    return normalized


def fetch_official(output_path=OFFICIAL_PATH, raw_path=RAW_AUDIT_PATH):
    start = OFFICIAL_START
    payloads = []
    frames = []
    for _ in range(10):
        url = f"{CNN_BASE_URL}/{start.date()}"
        response = requests.get(url, headers=CNN_HEADERS, timeout=60)
        response.raise_for_status()
        payload = response.json()
        frame = parse_official_payload(payload)
        payloads.append(payload)
        frames.append(frame)
        latest = frame["ObservationDate"].max()
        current_timestamp = pd.to_datetime(
            payload.get("fear_and_greed", {}).get("timestamp"),
            errors="coerce",
            utc=True,
        )
        current_date = (
            current_timestamp.tz_localize(None).normalize()
            if pd.notna(current_timestamp)
            else pd.Timestamp.utcnow().tz_localize(None).normalize()
        )
        if latest >= current_date - pd.Timedelta(days=7):
            break
        next_start = latest + pd.Timedelta(days=1)
        if next_start <= start:
            raise ValueError("CNN historical pagination made no progress")
        start = next_start
    else:
        raise ValueError("CNN historical pagination exceeded ten requests")
    raw_path = Path(raw_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(payloads, ensure_ascii=False),
        encoding="utf-8",
    )
    normalized = pd.concat(frames, ignore_index=True).drop_duplicates(
        "ObservationDate", keep="last"
    ).sort_values("ObservationDate")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_path, index=False)
    return load_point_in_time_signal(
        output_path,
        minimum=0,
        maximum=100,
        expected_methodology_version=OFFICIAL_VERSION,
    )


def _load_asset(ticker):
    frame = pd.read_csv(
        DATA_DIR / f"{ticker}.csv",
        index_col="Date",
        parse_dates=True,
    )
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
    return Indicator.add_indicators(frame)


def causal_percentile(series):
    return series.rolling(
        PERCENTILE_WINDOW,
        min_periods=PERCENTILE_MIN_PERIODS,
    ).apply(
        lambda values: float(np.mean(values <= values[-1])),
        raw=True,
    )


def build_four_component_proxy():
    spy = _load_asset("SPY")
    bnd = _load_asset("BND")
    hyg = _load_asset("HYG")
    lqd = _load_asset("LQD")
    vix = _load_asset("VIX")
    safe_haven = pd.concat(
        [spy["Close"].rename("SPY"), bnd["Close"].rename("BND")],
        axis=1,
        join="inner",
    )
    junk_bond = pd.concat(
        [hyg["Close"].rename("HYG"), lqd["Close"].rename("LQD")],
        axis=1,
        join="inner",
    )
    raw_components = {
        "MarketMomentumRaw": (
            spy["Close"] / spy["Close"].rolling(125).mean() - 1.0
        ),
        "MarketVolatilityRaw": -(
            vix["Close"] / vix["Close"].rolling(50).mean() - 1.0
        ),
        "SafeHavenDemandRaw": (
            safe_haven["SPY"].pct_change(20)
            - safe_haven["BND"].pct_change(20)
        ),
        "JunkBondDemandRaw": (
            junk_bond["HYG"].pct_change(20)
            - junk_bond["LQD"].pct_change(20)
        ),
    }
    score_components = {
        column.replace("Raw", "Score"): causal_percentile(series) * 100.0
        for column, series in raw_components.items()
    }
    components = pd.concat(
        {**raw_components, **score_components},
        axis=1,
        sort=True,
    ).sort_index()
    score_columns = list(score_components)
    components["RawComposite"] = components[score_columns].mean(
        axis=1,
        skipna=False,
    )
    components["ComponentCoverage"] = components[score_columns].notna().sum(
        axis=1
    )
    return components


def fit_affine_calibration(proxy, official, train_end=TRAIN_END):
    overlap = pd.concat(
        [proxy.rename("RawComposite"), official.rename("Official")],
        axis=1,
        join="inner",
    ).dropna()
    train = overlap.loc[:train_end]
    if len(train) < 100:
        raise ValueError("Insufficient overlap to calibrate reconstruction")
    design = np.column_stack(
        [np.ones(len(train)), train["RawComposite"].to_numpy()]
    )
    intercept, slope = np.linalg.lstsq(
        design,
        train["Official"].to_numpy(),
        rcond=None,
    )[0]
    overlap["Reconstructed"] = np.clip(
        intercept + slope * overlap["RawComposite"],
        0.0,
        100.0,
    )
    return float(intercept), float(slope), overlap


def _period_metrics(frame, period, start=None, end=None):
    sample = frame.loc[start:end].dropna()
    actual_extreme = sample["Official"] <= 20
    predicted_extreme = sample["Reconstructed"] <= 20
    true_positive = int((actual_extreme & predicted_extreme).sum())
    predicted_count = int(predicted_extreme.sum())
    actual_count = int(actual_extreme.sum())
    return {
        "Period": period,
        "StartDate": sample.index.min(),
        "EndDate": sample.index.max(),
        "Observations": len(sample),
        "Correlation": sample["Official"].corr(sample["Reconstructed"]),
        "MAE": (sample["Official"] - sample["Reconstructed"]).abs().mean(),
        "RMSE": np.sqrt(
            ((sample["Official"] - sample["Reconstructed"]) ** 2).mean()
        ),
        "OfficialExtremeFearDays": actual_count,
        "ReconstructedExtremeFearDays": predicted_count,
        "ExtremeFearPrecision": (
            true_positive / predicted_count if predicted_count else np.nan
        ),
        "ExtremeFearRecall": (
            true_positive / actual_count if actual_count else np.nan
        ),
    }


def reconstruction_reports(official_signal, components):
    official = official_signal.set_index("ObservationDate")["Value"]
    intercept, slope, overlap = fit_affine_calibration(
        components["RawComposite"], official
    )
    reconstructed = components.dropna(subset=["RawComposite"]).copy()
    reconstructed["Value"] = np.clip(
        intercept + slope * reconstructed["RawComposite"],
        0.0,
        100.0,
    )
    normalized = pd.DataFrame({
        "ObservationDate": reconstructed.index,
        "AvailableDate": reconstructed.index,
        "Value": reconstructed["Value"],
        "MethodologyVersion": RECONSTRUCTED_VERSION,
        "ComponentCoverage": reconstructed["ComponentCoverage"],
    })
    normalized.to_csv(RECONSTRUCTED_PATH, index=False)
    metrics = pd.DataFrame([
        _period_metrics(overlap, "CALIBRATION", end=TRAIN_END),
        _period_metrics(overlap, "HOLDOUT", start=HOLDOUT_START),
        _period_metrics(overlap, "FULL_OVERLAP"),
    ])
    holdout = metrics.loc[metrics["Period"] == "HOLDOUT"].iloc[0]
    decision = pd.DataFrame([{
        "Reconstruction": RECONSTRUCTED_VERSION,
        "AvailableComponents": 4,
        "CNNComponents": 7,
        "CalibrationIntercept": intercept,
        "CalibrationSlope": slope,
        "HoldoutCorrelation": holdout["Correlation"],
        "HoldoutMAE": holdout["MAE"],
        "HoldoutExtremeFearPrecision": holdout["ExtremeFearPrecision"],
        "HoldoutExtremeFearRecall": holdout["ExtremeFearRecall"],
        "CorrelationPass": holdout["Correlation"] >= 0.75,
        "MAEPass": holdout["MAE"] <= 10.0,
        "ExtremeFearPass": (
            holdout["ExtremeFearPrecision"] >= 0.70
            and holdout["ExtremeFearRecall"] >= 0.70
        ),
    }])
    decision["OverallPass"] = decision[
        ["CorrelationPass", "MAEPass", "ExtremeFearPass"]
    ].all(axis=1)
    overlap_report = overlap.reset_index().rename(
        columns={"index": "Date"}
    )
    return {
        "cnn_reconstruction_metrics": metrics,
        "cnn_reconstruction_decision": decision,
        "cnn_reconstruction_overlap": overlap_report,
    }


def run_reconstruction_audit(refresh_official=True):
    if refresh_official or not OFFICIAL_PATH.exists():
        official = fetch_official()
    else:
        official = load_point_in_time_signal(
            OFFICIAL_PATH,
            minimum=0,
            maximum=100,
            expected_methodology_version=OFFICIAL_VERSION,
        )
    components = build_four_component_proxy()
    reports = reconstruction_reports(official, components)
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    audit_reports = run_reconstruction_audit()
    print(audit_reports["cnn_reconstruction_metrics"].to_string(index=False))
    print("\nDecision")
    print(audit_reports["cnn_reconstruction_decision"].to_string(index=False))
