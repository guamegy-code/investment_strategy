"""
download_data.py

ETF 데이터를 다운로드하고
보조지표를 계산한 후 CSV로 저장한다.
"""

import pandas as pd
import yfinance as yf

from config import (
    DATA_DIR,
    TICKERS,
    START_DATE,
    END_DATE,
)

from indicators import Indicator


def download_one(ticker: str) -> pd.DataFrame:
    """
    ETF 하나 다운로드
    """

    print(f"Downloading {ticker}...")

    df = yf.download(
        ticker,
        start=START_DATE,
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
    filename = DATA_DIR / f"{ticker}.csv"
    df.to_csv(filename)
    print(f"저장 완료 : {filename}")
    return df


def main():

    print("=" * 60)
    print("ETF 데이터 다운로드")
    print("=" * 60)

    for ticker in TICKERS:
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

