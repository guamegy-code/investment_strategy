"""
strategy.py

투자 전략 모듈 (v2.2)

전략:
- QQQ RSI
- 이동평균(MA)
- 목표 비중 반환
"""


class Strategy:

    def __init__(self):
        pass


    def target_allocation(self, signal):

        """
        QQQ 상태를 판단하여
        목표 비중 반환
        """
        rsi = signal["RSI14"]
        close = signal["Close"]
        ma200 = signal["MA200"]

        # -----------------------------
        # 상승 추세
        # -----------------------------
        if (close > ma200 and rsi < 75):
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }

        # -----------------------------
        # 과열 구간
        # -----------------------------
        elif rsi >= 75:
            return {
                "QQQ": 0.3,
                "BND": 0.5,
                "GLD": 0.2,
            }

        # -----------------------------
        # 하락 방어
        # -----------------------------
        elif (close < ma200 or rsi <= 20):
            return {
                "QQQ": 0.2,
                "BND": 0.6,
                "GLD": 0.2,
            }

        # -----------------------------
        # 기본
        # -----------------------------
        else:
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }
        

class Strategy2:

    def __init__(self):
        pass


    def target_allocation(self, signal):

        """
        QQQ 상태를 판단하여
        목표 비중 반환
        """
        rsi = signal["RSI14"]
        close = signal["Close"]
        ma55 = signal["MA55"]

        # -----------------------------
        # 상승 추세
        # -----------------------------
        if (close > ma55 and rsi < 75):
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }

        # -----------------------------
        # 과열 구간
        # -----------------------------
        elif rsi >= 75:
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }

        # -----------------------------
        # 하락 방어
        # -----------------------------
        elif (close < ma55 or rsi <= 20):
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }

        # -----------------------------
        # 기본
        # -----------------------------
        else:
            return {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }