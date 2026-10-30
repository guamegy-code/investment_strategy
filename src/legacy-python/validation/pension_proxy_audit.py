"""Audit free long-history references; never fabricate an active ETF backtest."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_DIR, PROJECT_ROOT, RESEARCH_DATA_DIR
from result_paths import research_result_path


CACHE = RESEARCH_DATA_DIR / "pension_proxy"
STUDY_END = "2026-07-31"
REFERENCES = {
    "QQQ": "Nasdaq-100 USD reference, not the Korean execution price",
    "ONEQ": "Nasdaq Composite USD reference, not KoAct's historical portfolio",
    "IWF": "US growth equity style stress, not a replacement benchmark",
    "XLK": "Technology concentration stress, not a replacement benchmark",
    "133690.KS": "Older domestic Nasdaq-100 implementation reference",
    "KRW=X": "Daily USD/KRW reference; intraday timestamps are not synchronized",
}


def validate_prices(frame, name):
    """Reject empty, reordered, duplicate, nonfinite and nonpositive prices."""
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"No dated prices: {name}")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Invalid price dates: {name}")
    prices = frame[["Open", "Close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError(f"Invalid prices: {name}")
    return frame


def read_prices(path):
    frame = pd.read_csv(path, index_col="Date", parse_dates=True)
    return validate_prices(frame, str(path))


def observed_prices(frame, name):
    """Record missing-price sessions; retain their raw actions, never fill prices."""
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Invalid raw dates: {name}")
    missing = frame[["Open", "Close"]].isna().any(axis=1)
    dates = [str(date.date()) for date in frame.index[missing]]
    return validate_prices(frame.loc[~missing].copy(), name), dates


def refresh(latest_only=False):
    """Explicit network operation; default CLI only reads existing local caches."""
    import yfinance as yf

    CACHE.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))
    records = []
    for ticker, role in REFERENCES.items():
        frame = yf.download(
            ticker, start="2026-10-19" if latest_only else "1993-01-01",
            end="2026-10-24" if latest_only else "2026-08-01",
            auto_adjust=True, actions=True, progress=False, multi_level_index=False,
        )
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "Date"
        raw_path = CACHE / (ticker + ("_latest_raw.csv" if latest_only else "_raw.csv"))
        frame.to_csv(raw_path)
        dividend_rows = int(frame.get("Dividends", pd.Series(dtype=float)).ne(0).sum())
        frame, missing = observed_prices(frame, ticker)
        path = CACHE / (ticker + ("_latest.csv" if latest_only else ".csv"))
        frame.to_csv(path)
        records.append({
            "Ticker": ticker, "Role": role, "Start": str(frame.index[0].date()),
            "End": str(frame.index[-1].date()), "Rows": len(frame),
            "RawDividendRows": dividend_rows, "MissingPriceDates": missing,
            "Path": str(path.relative_to(PROJECT_ROOT)),
            "SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "RawPath": str(raw_path.relative_to(PROJECT_ROOT)),
            "RawSHA256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "RetrievedUTC": datetime.now(timezone.utc).isoformat(),
            "Source": "Yahoo Finance via yfinance; adjusted OHLC; actions=True",
            "AvailabilityOnly": bool(latest_only),
        })
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)
        name = "latest_availability.json" if latest_only else "availability.json"
        (CACHE / name).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_fred_fx():
    """Historical valuation reference only: H.10 daily observations publish weekly."""
    import requests

    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS&cosd=1981-01-01&coed=2026-07-31"
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    raw = pd.read_csv(StringIO(response.text), na_values=".")
    dates = pd.to_datetime(raw.iloc[:, 0])
    values = pd.to_numeric(raw["DEXKOUS"], errors="raise")
    frame = pd.DataFrame({"ReferenceRate": values.to_numpy()}, index=pd.DatetimeIndex(dates))
    frame.index.name = "Date"
    frame = frame.loc[:STUDY_END]
    if frame.empty or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Invalid FRED reference dates")
    observed = frame.dropna()
    if observed.empty or not np.isfinite(observed.to_numpy()).all() or (observed <= 0).any().any():
        raise ValueError("Invalid FRED reference rates")
    CACHE.mkdir(parents=True, exist_ok=True)
    raw_path, path = CACHE / "DEXKOUS_raw.csv", CACHE / "DEXKOUS.csv"
    raw_path.write_text(response.text, encoding="utf-8")
    observed.to_csv(path)
    record = {
        "Series": "DEXKOUS", "Start": str(observed.index[0].date()),
        "End": str(observed.index[-1].date()), "Rows": len(observed),
        "MissingObservationRows": int(frame.ReferenceRate.isna().sum()),
        "SourceURL": url, "RetrievedUTC": datetime.now(timezone.utc).isoformat(),
        "SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "RawSHA256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "Meaning": "KRW per USD, New York noon reference, not a Korean opening execution price",
        "ReleaseTiming": "H.10 publishes daily observations weekly; not a same-day live signal",
    }
    (CACHE / "fred_availability.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False), flush=True)


def aligned_returns(target, reference):
    """Pair common observed prices first: both returns span identical dates.

    Intended for Korean ETFs with the same exchange clock. No padding, backfill,
    US same-date close, or pre-inception synthetic history is used here.
    """
    prices = pd.concat({"Target": target, "Reference": reference}, axis=1, join="inner").dropna()
    if len(prices) < 3 or not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError("Insufficient or invalid observed overlap")
    return prices.pct_change(fill_method=None).iloc[1:]


def proxy_diagnostics(returns, training_rows=126):
    """Descriptive fit and an early-fit/later-check; not independent strategy OOS."""
    if training_rows < 3 or len(returns) <= training_rows:
        raise ValueError("Insufficient calibration and later observations")
    x, y = returns["Reference"], returns["Target"]
    train, later = returns.iloc[:training_rows], returns.iloc[training_rows:]
    design = np.column_stack([np.ones(training_rows), train.Reference])
    if np.linalg.matrix_rank(design) != 2 or x.std(ddof=1) <= 0:
        raise ValueError("Reference has no identifiable variation")
    alpha, beta = np.linalg.lstsq(design, train.Target.to_numpy(), rcond=None)[0]
    prediction = alpha + beta * later.Reference
    sse = float(((later.Target - prediction) ** 2).sum())
    sst = float(((later.Target - later.Target.mean()) ** 2).sum())
    corr = float(x.corr(y))
    rolling_beta = y.rolling(63).cov(x) / x.rolling(63).var()
    active = y - x
    return {
        "ReturnStart": str(returns.index[0].date()), "End": str(returns.index[-1].date()),
        "ReturnRows": len(returns), "DailyCorrelation": corr, "FullSampleR2": corr ** 2,
        "FullSampleBeta": float(y.cov(x) / x.var()),
        "VolatilityRatio": float(y.std(ddof=1) / x.std(ddof=1)),
        "TrackingErrorAnnual": float(active.std(ddof=1) * np.sqrt(252)),
        "Rolling63BetaMin": float(rolling_beta.min()), "Rolling63BetaMax": float(rolling_beta.max()),
        "TrainingRows": len(train), "TrainingEnd": str(train.index[-1].date()),
        "TrainingAlphaDaily": float(alpha), "TrainingBeta": float(beta),
        "LaterStart": str(later.index[0].date()), "LaterRows": len(later),
        "LaterR2FromFrozenFit": None if sst <= 0 else 1 - sse / sst,
        "LaterErrorVolAnnual": float((later.Target - prediction).std(ddof=1) * np.sqrt(252)),
        "Interpretation": "Descriptive proxy audit; no strategy performance or pre-inception fund return",
    }


def audit():
    coverage, inputs = [], {}
    availability_path = CACHE / "availability.json"
    availability = {row["Ticker"]: row for row in json.loads(availability_path.read_text(encoding="utf-8"))}
    if set(availability) != set(REFERENCES):
        raise ValueError("Incomplete reference download")
    inputs[str(availability_path.relative_to(PROJECT_ROOT))] = hashlib.sha256(availability_path.read_bytes()).hexdigest()
    for ticker, role in REFERENCES.items():
        path = CACHE / (ticker + ".csv")
        frame = read_prices(path)
        if frame.index[-1] > pd.Timestamp(STUDY_END):
            raise ValueError(f"Study cache crosses fixed cutoff: {ticker}")
        inputs[str(path.relative_to(PROJECT_ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        if inputs[str(path.relative_to(PROJECT_ROOT))] != availability[ticker]["SHA256"]:
            raise ValueError(f"Reference cache changed after availability audit: {ticker}")
        raw_path = PROJECT_ROOT / availability[ticker]["RawPath"]
        raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if raw_hash != availability[ticker]["RawSHA256"]:
            raise ValueError(f"Raw reference cache changed: {ticker}")
        inputs[str(raw_path.relative_to(PROJECT_ROOT))] = raw_hash
        coverage.append({"Ticker": ticker, "Role": role, "Start": str(frame.index[0].date()),
                         "End": str(frame.index[-1].date()), "Rows": len(frame),
                         "MissingPriceDates": ";".join(availability[ticker]["MissingPriceDates"])})
    reference = read_prices(CACHE / "133690.KS.csv").Close.loc[:STUDY_END]
    targets = {ticker: DATA_DIR / (ticker + ".csv") for ticker in ("379810.KS", "0015B0.KS")}
    common_start = max(read_prices(path).index[0] for path in targets.values())
    rows = []
    for ticker, path in targets.items():
        frame = read_prices(path)
        inputs[str(path.relative_to(PROJECT_ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        # Both products are judged on exactly the same overlap, not different regimes.
        paired = aligned_returns(frame.Close.loc[common_start:STUDY_END], reference)
        rows.append({"Target": ticker, "Reference": "133690.KS", **proxy_diagnostics(paired)})
    pd.DataFrame(coverage).to_csv(research_result_path("pension_proxy_coverage.csv"), index=False)
    pd.DataFrame(rows).to_csv(research_result_path("pension_proxy_diagnostics.csv"), index=False)
    for filename in ("latest_availability.json", "fred_availability.json", "DEXKOUS.csv", "DEXKOUS_raw.csv"):
        evidence = CACHE / filename
        if evidence.exists():
            inputs[str(evidence.relative_to(PROJECT_ROOT))] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    manifest = {
        "StudyEnd": STUDY_END, "CreatedUTC": datetime.now(timezone.utc).isoformat(),
        "InputsSHA256": inputs, "CodeSHA256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "PriceClock": "KRX close vs KRX close for diagnostics; foreign data coverage only",
        "NoSyntheticFundHistory": True, "NoStrategyBacktest": True,
        "LatestAvailabilityFile": str((CACHE / "latest_availability.json").relative_to(PROJECT_ROOT)),
    }
    research_result_path("pension_proxy_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pd.DataFrame(coverage).to_string(index=False))
    print(pd.DataFrame(rows).to_string(index=False))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true")
    group.add_argument("--latest-only", action="store_true")
    group.add_argument("--fred-fx", action="store_true")
    args = parser.parse_args()
    if args.fred_fx:
        refresh_fred_fx()
    elif args.download or args.latest_only:
        refresh(latest_only=args.latest_only)
    else:
        audit()


if __name__ == "__main__":
    main()
