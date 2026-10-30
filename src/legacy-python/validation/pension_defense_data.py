"""Explicit public daily-price cache refresh for the pension defense study."""
import hashlib
import json
import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf

from config import PROJECT_ROOT, RESEARCH_DATA_DIR

CACHE = RESEARCH_DATA_DIR / "pension_defense"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--risk-only", action="store_true")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))
    rows = []
    tickers = ("379810.KS", "0015B0.KS") if args.risk_only else ("153130.KS", "488770.KS", "411060.KS")
    for ticker in tickers:
        frame = yf.download(ticker, start="2026-10-19" if args.latest_only else "2010-01-01",
            end="2026-10-26" if args.latest_only else "2026-08-01",
            auto_adjust=True, actions=True, progress=False, multi_level_index=False)
        if frame.empty:
            raise ValueError(f"No public data for {ticker}")
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "Date"
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f"Invalid dates: {ticker}")
        if frame[["Open", "Close"]].isna().any().any() or (frame[["Open", "Close"]] <= 0).any().any():
            raise ValueError(f"Invalid prices: {ticker}")
        path = CACHE / (ticker + ".csv")
        if not args.latest_only:
            frame.to_csv(path)
        rows.append({"Ticker": ticker, "Start": str(frame.index[0].date()),
            "End": str(frame.index[-1].date()), "Rows": len(frame),
            "DividendRows": int(frame.get("Dividends", pd.Series(dtype=float)).ne(0).sum()),
            "Source": "Yahoo Finance public daily history via yfinance, auto_adjust=True",
            "StudyCacheSHA256" if args.latest_only else "SHA256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print(rows[-1], flush=True)
    name = ("risk_" if args.risk_only else "") + ("latest_availability.json" if args.latest_only else "availability.json")
    (CACHE / name).write_text(
        json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
