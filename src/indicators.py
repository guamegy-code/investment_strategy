"""
indicator.py

기술적 지표 계산
"""

import numpy as np
import pandas as pd


class Indicator:

    # ==================================================
    # Moving Average
    # ==================================================
    @staticmethod
    def add_ma(df):
        for period in [20, 55, 120, 200]:
            df[f"MA{period}"] = df["Close"].rolling(period).mean()
        return df

    # ==================================================
    # EMA
    # ==================================================
    @staticmethod
    def add_ema(df):
        for period in [20, 55, 120, 200]:
            df[f"EMA{period}"] = df["Close"].ewm(span=period, adjust=False).mean()
        return df

    # ==================================================
    # RSI
    # ==================================================
    @staticmethod
    def add_rsi(df, period=14):
        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()

        rs = avg_gain / avg_loss
        df["RSI14"] = 100 - (100 / (1 + rs))
        return df

    # ==================================================
    # MACD
    # ==================================================
    @staticmethod
    def add_macd(df):
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]
        return df
    
    # ==================================================
    # Stochastic Oscillator
    # ==================================================

    @staticmethod
    def add_stochastic(df, period=14, smooth=3):
        lowest_low = df["Low"].rolling(period).min()
        highest_high = df["High"].rolling(period).max()
        df["STOCH_K"] = (df["Close"] - lowest_low) / (highest_high - lowest_low) * 100
        df["STOCH_D"] = df["STOCH_K"].rolling(smooth).mean()
        return df

    # ==================================================
    # Rate of Change (ROC)
    # ==================================================
    @staticmethod
    def add_roc(df, period=252):
        df[f"ROC{period}"] = df["Close"].pct_change(period) * 100
        return df

    # ==================================================
    # ATR
    # ==================================================
    @staticmethod
    def add_atr(df, period=14):
        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([ high_low, high_close, low_close ], axis=1).max(axis=1)
        df["TR"] = tr
        df["ATR"] = tr.rolling(period).mean()
        df["ATR60"] = df["ATR"].rolling(60).mean()
        return df

    # ==================================================
    # Bollinger Band
    # ==================================================
    @staticmethod
    def add_bollinger(df, period=20, std=2):
        ma = df["Close"].rolling(period).mean()
        sigma = df["Close"].rolling(period).std()
        df["BB_MIDDLE"] = ma
        df["BB_UPPER"] = ma + sigma * std
        df["BB_LOWER"] = ma - sigma * std
        return df
    
    # ==================================================
    # Rolling Volatility
    # ==================================================
    @staticmethod
    def add_volatility(df, period=60):
        returns = df["Close"].pct_change()
        df[f"VOL{period}"] = returns.rolling(period).std() * np.sqrt(252)
        return df

    # ==================================================
    # Rolling Maximum Drawdown
    # ==================================================
    @staticmethod
    def add_mdd(df, period=252):
        rolling_max = df["Close"].rolling(period).max()
        drawdown = (df["Close"] - rolling_max) / rolling_max
        df[f"MDD{period}"] = drawdown.rolling(period).min()
        return df

    # ==================================================
    # Add All Indicators
    # ==================================================
    @classmethod
    def add_indicators(cls, df):
        df = cls.add_ma(df)
        df = cls.add_ema(df)
        df = cls.add_rsi(df)
        df = cls.add_macd(df)
        df = cls.add_stochastic(df)
        df = cls.add_roc(df)
        df = cls.add_atr(df)
        df = cls.add_bollinger(df)
        df = cls.add_volatility(df)
        df = cls.add_mdd(df)
        return df    