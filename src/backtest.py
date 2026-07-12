"""
backtest.py

백테스트 엔진 (v2.2)

역할:
- ETF 데이터 로딩
- 날짜별 투자 시뮬레이션
- 전략 실행
- 포트폴리오 기록
"""

from pathlib import Path
import pandas as pd

from config import (
    DATA_DIR,
    INITIAL_CASH,
    TICKERS,
)
from portfolio import Portfolio
from strategy import Strategy, Strategy2


class Backtest:

    def __init__(self):

        self.tickers = TICKERS

        self.strategy = Strategy2()

        self.portfolio = Portfolio(
            initial_cash=INITIAL_CASH,
            tickers=self.tickers
        )

        self.data = self.load_data()


    # ==================================================
    # 데이터 로딩
    # ==================================================

    def load_one(self, ticker):
        """
        ETF 하나 로딩
        """

        file = DATA_DIR / f"{ticker}.csv"
        df = pd.read_csv(
            file,
            index_col="Date",
            parse_dates=True
        )
        return df


    # ==================================================
    # 전체 ETF 데이터 결합
    # ==================================================

    def load_data(self):

        """
        QQQ/BND/GLD 데이터 병합

        결과 예:

        QQQ_Close
        QQQ_MA20
        QQQ_RSI14

        BND_Close
        GLD_Close
        """

        merged = None

        for ticker in self.tickers:

            df = self.load_one(ticker)
            df = df.add_prefix(f"{ticker}_")

            if merged is None:
                merged = df
            else:
                merged = merged.join(df,how="inner")

        merged.sort_index(inplace=True)

        return merged


    # ==================================================
    # 가격 딕셔너리 생성
    # ==================================================

    def get_prices(self, row):
        """
        현재 가격 생성
        """

        prices = {}

        for ticker in self.tickers:
            prices[ticker] = row[f"{ticker}_Close"]

        return prices
    
    
    # ==================================================
    # 전략 입력 데이터 생성
    # ==================================================

    def get_signal_row(self, row):
        """
        전략에 전달할 QQQ 데이터
        전략은 QQQ의 MA 혹은 RSI로 판단
        """

        signal = pd.Series(
            {
                "Close": row["QQQ_Close"],
                "MA20": row["QQQ_MA20"],
                "MA55": row["QQQ_MA55"],
                "MA120": row["QQQ_MA120"],
                "MA200": row["QQQ_MA200"],
                "RSI14": row["QQQ_RSI14"],
            }
        )

        return signal


    # ==================================================
    # 월말 여부 확인
    # ==================================================

    def is_month_end(self, index):
        """
        현재 날짜가 월말 거래일인지 확인
        """
        next_day = index + pd.Timedelta(days=1)
        return (next_day.month != index.month)


    # ==================================================
    # 백테스트 실행
    # ==================================================

    def run(self):

        """
        전체 백테스트 실행
        반환: portfolio history
        """

        first_trade = True

        for date, row in self.data.iterrows():
            prices = self.get_prices(row)
            signal = self.get_signal_row(row)

            # ------------------------------------------
            # 최초 투자
            # ------------------------------------------
            if first_trade:
                target_weights = self.strategy.target_allocation(signal)
                self.portfolio.rebalance(prices, target_weights, date)
                first_trade = False

            # ------------------------------------------
            # 월말 리밸런싱
            # ------------------------------------------
            elif self.is_month_end(date):
                target_weights = self.strategy.target_allocation(signal)
                self.portfolio.rebalance(prices, target_weights, date)

            # ------------------------------------------
            # 매일 평가 기록
            # ------------------------------------------
            self.portfolio.record(date, prices)

        return pd.DataFrame(self.portfolio.get_history())
    

    # ==================================================
    # 포트폴리오 결과 반환
    # ==================================================

    def get_result(self):

        """
        백테스트 결과 반환
        """
        result = pd.DataFrame(self.portfolio.get_history())

        if not result.empty:
            result.set_index("Date", inplace=True)

        return result


    # ==================================================
    # 거래 내역 반환
    # ==================================================

    def get_trades(self):

        """
        리밸런싱 기록 반환
        """
        return pd.DataFrame(self.portfolio.get_trades())


    # ==================================================
    # 전체 실행
    # ==================================================

    def run_all(self):

        """
        백테스트 실행 후 결과 반환
        """
        self.run()
        history = self.get_result()
        trades = self.get_trades()

        return (history, trades)