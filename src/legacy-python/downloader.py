"""
download_data.py

ETF 데이터를 다운로드하고
보조지표를 계산한 후 CSV로 저장한다.
"""

from pathlib import Path
import warnings

import pandas as pd
import yfinance as yf

from config import (
    DATA_DIR,
    FX_RATE_TICKERS,
    TICKERS,
    START_DATE,
    DATA_START_DATE,
    END_DATE,
)

from indicators import Indicator


KRW_ADJUSTED_SUFFIX = "_KRW"
HY_OAS_TICKER = "BAMLH0A0HYM2"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"


def _load_fred_series(source) -> pd.Series:
    """Read a FRED CSV, accepting both DATE and observation_date headers."""
    observations = pd.read_csv(source)
    if observations.shape[1] < 2:
        raise ValueError(f"FRED source has no observations: {source}")
    dates = pd.to_datetime(observations.iloc[:, 0], errors="coerce")
    values = pd.to_numeric(observations.iloc[:, 1], errors="coerce")
    series = pd.Series(values.to_numpy(), index=dates, dtype=float).dropna()
    series = series[~series.index.isna()].sort_index()
    if series.empty or series.index.has_duplicates:
        raise ValueError(f"FRED source is empty or has duplicate dates: {source}")
    return series


def _merge_historical_and_live_fred(
    historical: pd.Series, live: pd.Series, *, overlap_tolerance: float = 0.05,
) -> pd.Series:
    """Prefer revised live observations and flag material overlap differences."""
    overlap = historical.index.intersection(live.index)
    if len(overlap):
        difference = (historical.loc[overlap] - live.loc[overlap]).abs()
        if difference.max() > overlap_tolerance:
            warnings.warn(
                f"FRED historical/live overlap differs by up to {difference.max():.3f} pp",
                stacklevel=2,
            )
    merged = pd.concat((historical, live))
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def _align_macro_to_qqq_sessions(
    observations: pd.Series, qqq_dates: pd.DatetimeIndex, *, lag_sessions: int = 1,
) -> pd.Series:
    if lag_sessions < 0:
        raise ValueError("lag_sessions must be nonnegative")
    return observations.reindex(qqq_dates, method="ffill").shift(lag_sessions)


def _load_hy_oas_observations(historical_path: Path | None = None) -> pd.Series:
    historical_path = historical_path or (
        Path(__file__).resolve().parents[2] / "tmp" / f"{HY_OAS_TICKER}_historical.csv"
    )
    if not historical_path.is_file():
        raise FileNotFoundError(
            f"HY OAS research requires a private pre-2026 FRED archive at {historical_path}"
        )
    historical = _load_fred_series(historical_path)
    if historical.index.min() > pd.Timestamp("1997-01-31"):
        raise ValueError("HY OAS archive does not reach the 1996/1997 history")
    try:
        live = _load_fred_series(FRED_URL.format(HY_OAS_TICKER))
    except (OSError, ValueError) as error:
        cached_tail = historical_path.parent / f"{HY_OAS_TICKER}.csv"
        if not cached_tail.is_file():
            warnings.warn(f"Current HY OAS unavailable; using archive only: {error}", stacklevel=2)
            return historical
        live = _load_fred_series(cached_tail)
        warnings.warn(
            f"Current HY OAS unavailable; using cached tail through "
            f"{live.index.max().date()}: {error}",
            stacklevel=2,
        )
    return _merge_historical_and_live_fred(historical, live)


def _macro_market_frame(observations: pd.Series, qqq_dates: pd.DatetimeIndex) -> pd.DataFrame:
    spread = _align_macro_to_qqq_sessions(observations, qqq_dates)
    frame = pd.DataFrame(index=qqq_dates)
    for field in ("Open", "High", "Low", "Close"):
        frame[field] = spread
    frame["Volume"] = 0
    frame = Indicator.add_indicators(frame.dropna(subset=["Close"]))
    frame.index.name = "Date"
    return frame


def _load_saved_prices(data_dir: Path, ticker: str) -> pd.DataFrame:
    """Read the OHLC source used to build a synthetic KRW price series."""
    frame = pd.read_csv(data_dir / f"{ticker}.csv", index_col="Date", parse_dates=True)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame.sort_index()


def _build_krw_adjusted_asset(ticker: str, output_dir: Path) -> pd.DataFrame:
    """Create a KRW total-return proxy from an investable foreign asset.

    The Korean-listed assets already trade in KRW.  For US and global ETFs we
    explicitly apply the daily USD/KRW close so their signals and portfolio
    values are on the same currency basis as Strategy 16's Korean assets.
    """
    base_ticker = ticker.removesuffix(KRW_ADJUSTED_SUFFIX)
    if not base_ticker:
        raise ValueError(f"invalid KRW-adjusted ticker: {ticker}")
    ensure_data_files((base_ticker, "KRW=X"), data_dir=output_dir)
    base = _load_saved_prices(output_dir, base_ticker)
    fx = _load_saved_prices(output_dir, "KRW=X")["Close"].rename("FX")
    source_columns = ["Open", "High", "Low", "Close"]
    if not set(source_columns).issubset(base.columns):
        raise ValueError(f"{base_ticker} is missing OHLC source columns")

    combined = base[source_columns].join(fx, how="left")
    # The FX series does not quote every Korean/US holiday.  A preceding
    # published FX close is the last observable conversion rate; never fill
    # the beginning, where no rate was known yet.
    combined["FX"] = combined["FX"].ffill()
    combined = combined.dropna(subset=["FX"])
    adjusted = combined[source_columns].mul(combined["FX"], axis=0)
    adjusted["Volume"] = base.loc[adjusted.index, "Volume"] if "Volume" in base else 0
    adjusted.index.name = "Date"
    adjusted = Indicator.add_indicators(adjusted)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / f"{ticker}.csv"
    adjusted.to_csv(filename)
    print(f"Built KRW-adjusted proxy: {filename}")
    return adjusted


def download_one(ticker: str, output_dir=DATA_DIR) -> pd.DataFrame:
    """
    ETF 하나 다운로드
    """

    from tdf_proxy import (
        TDF_PROXY_COMPONENT_WEIGHTS,
        TDF_PROXY_TICKER,
        build_tdf2050_proxy,
    )

    output_dir = Path(output_dir)

    if ticker.endswith(KRW_ADJUSTED_SUFFIX):
        return _build_krw_adjusted_asset(ticker, output_dir)

    if ticker == TDF_PROXY_TICKER:
        ensure_data_files(TDF_PROXY_COMPONENT_WEIGHTS, data_dir=output_dir)
        print(f"Building {TDF_PROXY_TICKER}...")
        return build_tdf2050_proxy(data_dir=output_dir)

    if ticker in ("BAA10Y", HY_OAS_TICKER):
        # FRED observations are not guaranteed to be known at that day's
        # equity close. Align first, then expose the previous QQQ session.
        ensure_data_files(("QQQ",), data_dir=output_dir)
        qqq_dates = _load_saved_prices(output_dir, "QQQ").index
        if ticker == "BAA10Y":
            local_source = Path(__file__).resolve().parents[2] / "tmp" / "BAA10Y.csv"
            source = local_source if local_source.is_file() else FRED_URL.format(ticker)
            observations = _load_fred_series(source)
        else:
            observations = _load_hy_oas_observations()
        frame = _macro_market_frame(observations, qqq_dates)
        output_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output_dir / f"{ticker}.csv")
        return frame

    print(f"Downloading {ticker}...")

    df = yf.download(
        ticker,
        start=DATA_START_DATE,
        end=END_DATE,
        auto_adjust=True,
        progress=False,
        multi_level_index=False,      # 최신 yfinance 대응
    )

    if df.empty:
        raise ValueError(f"{ticker} 데이터 다운로드 실패")

    # 혹시라도 MultiIndex가 생성되면 제거
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Date 컬럼명 보장
    df.index.name = "Date"

    # 보조지표 추가
    df = Indicator.add_indicators(df)    
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / f"{ticker}.csv"
    df.to_csv(filename)
    print(f"저장 완료 : {filename}")
    return df


def _needs_indicator_refresh(path, fields):
    fields = {str(field).upper() for field in fields} - {"CLOSE"}
    if not fields:
        return False
    try:
        frame = pd.read_csv(path, index_col="Date", parse_dates=True)
    except (OSError, ValueError):
        return True
    if frame.empty:
        return True
    start = pd.Timestamp(START_DATE)
    first_date = frame.index.min()
    # 상장일이 백테스트 시작일보다 훨씬 늦으면 선행 데이터를 만들 수 없다.
    if first_date > start + pd.Timedelta(days=14):
        return False
    first_rows = frame.loc[frame.index >= start]
    if first_rows.empty:
        return True
    columns = {str(column).upper(): column for column in frame.columns}
    if not fields.issubset(columns):
        return True
    # Some valid assets (notably newer Korean ETFs and USD/KRW) have no
    # history before the requested backtest start.  They cannot have a
    # 252-day indicator on the very first common date, but rebuilding the
    # same file cannot create that unavailable history.  Accept the file once
    # a later usable row exists; Backtest.load_data will begin at that common
    # indicator-ready date.  A file with no usable row at all still refreshes.
    required_columns = [columns[field] for field in fields]
    return first_rows.dropna(subset=required_columns).empty


def ensure_data_files(
    tickers, data_dir=DATA_DIR, required_market_fields=None
):
    """Download missing files and refresh files lacking indicator warm-up."""
    data_dir = Path(data_dir)
    ordered_tickers = tuple(dict.fromkeys(tickers or ()))
    required_market_fields = required_market_fields or {}
    pending = tuple(
        ticker
        for ticker in ordered_tickers
        if (
            not (data_dir / f"{ticker}.csv").is_file()
            or _needs_indicator_refresh(
                data_dir / f"{ticker}.csv",
                required_market_fields.get(ticker, ()),
            )
        )
    )
    if not pending:
        return ()

    print(f"누락되었거나 선행 기간이 부족한 시장 데이터 다운로드: {', '.join(pending)}")
    failures = []
    for ticker in pending:
        try:
            download_one(ticker, output_dir=data_dir)
        except Exception as error:
            failures.append((ticker, error))

    if failures:
        detail = "; ".join(
            f"{ticker}: {error}" for ticker, error in failures
        )
        raise RuntimeError(f"시장 데이터 자동 다운로드 실패: {detail}")
    return pending


def main():

    print("=" * 60)
    print("ETF 데이터 다운로드")
    print("=" * 60)

    # 백테스트 종목과 차트의 환율 제거 표시에 필요한 환율을 함께 갱신한다.
    for ticker in (*TICKERS, *FX_RATE_TICKERS):
        try:
            df = download_one(ticker)

        except Exception as e:
            print(f"{ticker} 실패")
            print(e)

    print("=" * 60)
    print("완료")
    print("=" * 60)


if __name__ == "__main__":
    main()

