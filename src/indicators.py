"""
indicators.py

기술적 지표 계산
"""

import pandas as pd
import numpy as np

from config import (
    MA_SHORT,
    MA_MID,
    MA_LONG,
    MA_VERY_LONG,
    RSI_PERIOD,
)


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """
    단순이동평균(SMA)
    """
    return series.rolling(window=period).mean()


def calculate_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """
    RSI(Wilder 방식)
    """

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    ATR(Average True Range)
    """

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    return atr


def calculate_volatility(
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    """
    일간 변동성
    """

    returns = close.pct_change()

    volatility = returns.rolling(period).std()

    return volatility


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    모든 보조지표 계산
    """

    df = df.copy()

    # -------------------------
    # 이동평균
    # -------------------------

    df["MA20"] = calculate_sma(df["Close"], MA_SHORT)

    df["MA55"] = calculate_sma(df["Close"], MA_MID)

    df["MA120"] = calculate_sma(df["Close"], MA_LONG)

    df["MA200"] = calculate_sma(df["Close"], MA_VERY_LONG)

    # -------------------------
    # RSI
    # -------------------------

    df["RSI14"] = calculate_rsi(df["Close"])

    # -------------------------
    # ATR
    # -------------------------

    df["ATR14"] = calculate_atr(
        df["High"],
        df["Low"],
        df["Close"],
    )

    # -------------------------
    # 변동성
    # -------------------------

    df["Volatility20"] = calculate_volatility(df["Close"])

    return df