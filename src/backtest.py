"""
backtest.py

백테스트 엔진
"""

import pandas as pd

from config import (
    DATA_DIR,
    TICKERS,
)

from portfolio import Portfolio


class Backtest:

    def __init__(self, strategy):

        self.strategy = strategy
        self.tickers = TICKERS
        self.data = self.load_data()
        self.portfolio = Portfolio()


    # ==================================================
    # ETF 데이터 로딩
    # ==================================================
    def load_one(self, ticker):
        file = DATA_DIR / f"{ticker}.csv"
        df = pd.read_csv(
            file,
            index_col="Date",
            parse_dates=True
        )
        return df


    # ==================================================
    # 전체 데이터 병합
    # ==================================================
    def load_data(self):
        merged = None
        for ticker in self.tickers:
            df = self.load_one(ticker)
            df = df.add_prefix(f"{ticker}_")
            if merged is None:
                merged = df
            else:
                merged = merged.join(df, how="inner")
        merged.sort_index(inplace=True)
        return merged


    # ==================================================
    # 현재 가격
    # ==================================================
    def get_prices(self, row):
        prices = {}
        for ticker in self.tickers:
            prices[ticker] = row[f"{ticker}_Close"]
        return prices

 
    # ==================================================
    # 전략 입력 데이터
    # ==================================================
    def get_market(self, row):
        market = {}
        for ticker in self.tickers:
            market[ticker] = {
                "Close": row[f"{ticker}_Close"],
                "MA20": row.get(f"{ticker}_MA20"),
                "MA55": row.get(f"{ticker}_MA55"),
                "MA120": row.get(f"{ticker}_MA120"),
                "MA200": row.get(f"{ticker}_MA200"),
                "EMA20": row.get(f"{ticker}_EMA20"),
                "EMA55": row.get(f"{ticker}_EMA55"),
                "EMA120": row.get(f"{ticker}_EMA120"),
                "EMA200": row.get(f"{ticker}_EMA200"),
                "RSI14": row.get(f"{ticker}_RSI14"),
                "MACD": row.get(f"{ticker}_MACD"),
                "MACD_SIGNAL": row.get(f"{ticker}_MACD_SIGNAL"),
                "MACD_HIST": row.get(f"{ticker}_MACD_HIST"),
                "STOCH_K": row.get(f"{ticker}_STOCH_K"),
                "STOCH_D": row.get(f"{ticker}_STOCH_D"),
                "ROC252": row.get(f"{ticker}_ROC252"),
                "ATR": row.get(f"{ticker}_ATR"),
                "ATR60": row.get(f"{ticker}_ATR60"),
                "BB_UPPER": row.get(f"{ticker}_BB_UPPER"),
                "BB_MIDDLE": row.get(f"{ticker}_BB_MIDDLE"),
                "BB_LOWER": row.get(f"{ticker}_BB_LOWER"),
                "VOL60": row.get(f"{ticker}_VOL60"),
                "MDD252": row.get(f"{ticker}_MDD252"),
                "QQQ_BND": row.get("QQQ_BND"),
                "QQQ_GLD": row.get("QQQ_GLD"),
            }
        return market    
    

    # ==================================================
    # 백테스트 실행
    # ==================================================
    def run(self):

        first_trade = True

        for date, row in self.data.iterrows():
            prices = self.get_prices(row)
            market = self.get_market(row)
            signal  = self.strategy.evaluate(date, market, self.portfolio)

            # ------------------------------------------
            # 최초 투자
            # ------------------------------------------
            if first_trade:
                self.portfolio.start_rebalance(
                    signal["target"], days=signal["days"], date=date
                )
                first_trade = False

            # ------------------------------------------
            # 전략에 따른 리밸런싱
            # ------------------------------------------
            elif signal["rebalance"]:
                self.portfolio.start_rebalance(
                    signal["target"], signal["days"], date=date
                )

            self.portfolio.update(prices, date=date)

            # ------------------------------------------
            # 일별 기록
            # ------------------------------------------
            self.portfolio.record(date, prices)

        return self.get_result()
    

    # ==================================================
    # 결과 반환
    # ==================================================
    def get_result(self):
        history = pd.DataFrame(self.portfolio.get_history())
        if not history.empty:
            history.set_index("Date", inplace=True)
        return history


    # ==================================================
    # 거래 내역 반환
    # ==================================================
    def get_trades(self):
        trades = pd.DataFrame(self.portfolio.get_trades())
        return trades


    # ==================================================
    # 전체 실행
    # ==================================================
    def run_all(self):
        history = self.run()
        trades = self.get_trades()
        rebalances = self.portfolio.get_rebalances()
        return history, trades, rebalances
