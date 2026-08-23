"""
download_data.py

ETF 데이터를 다운로드하고
보조지표를 계산한 후 CSV로 저장한다.
"""

from pathlib import Path

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


def download_one(ticker: str, output_dir=DATA_DIR) -> pd.DataFrame:
    """
    ETF 하나 다운로드
    """

    from tdf_proxy import (
        TDF_PROXY_COMPONENT_WEIGHTS,
        TDF_PROXY_TICKER,
        build_tdf2050_proxy,
    )

    if ticker == TDF_PROXY_TICKER:
        ensure_data_files(TDF_PROXY_COMPONENT_WEIGHTS, data_dir=output_dir)
        print(f"Building {TDF_PROXY_TICKER}...")
        return build_tdf2050_proxy(data_dir=output_dir)

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
    output_dir = Path(output_dir)
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
    first = first_rows.iloc[0]
    return any(pd.isna(first[columns[field]]) for field in fields)


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

