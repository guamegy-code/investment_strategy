"""
backtest.py

백테스트 엔진
"""

import pandas as pd

from config import (
    COMMISSION,
    DATA_DIR,
    SLIPPAGE,
    TICKERS,
)

from indicators import Indicator
from portfolio import Portfolio


class Backtest:

    def __init__(
        self,
        strategy,
        data_dir=DATA_DIR,
        tickers=None,
        commission=COMMISSION,
        slippage=SLIPPAGE,
        signal_delay_days=0,
        bear_signal_delay_days=None,
    ):

        self.strategy = strategy
        self.data_dir = data_dir
        self.tickers = list(tickers or TICKERS)
        self.signal_delay_days = signal_delay_days
        self.bear_signal_delay_days = bear_signal_delay_days
        self.delayed_rebalance = None
        self.data = self.load_data()
        self.portfolio = Portfolio(
            commission=commission,
            slippage=slippage,
        )

    def _delay_for_signal(self):
        state = getattr(self.strategy, "state", None)
        if hasattr(state, "value"):
            state = state.value
        if state == "BEAR" and self.bear_signal_delay_days is not None:
            return self.bear_signal_delay_days
        return self.signal_delay_days

    def _queue_rebalance(self, signal, signal_date):
        delay = self._delay_for_signal()
        if delay <= 0:
            # An immediately actionable new signal invalidates any older order
            # that was still waiting for its execution date.
            self.delayed_rebalance = None
            self.portfolio.start_rebalance(
                signal["target"], signal["days"], date=signal_date,
                reason=signal.get("reason"),
            )
            return
        # A later signal supersedes an order that has not started executing.
        self.delayed_rebalance = {
            "remaining": delay,
            "target": signal["target"].copy(),
            "days": signal["days"],
            "signal_date": signal_date,
            "reason": signal.get("reason"),
        }

    def _activate_delayed_rebalance(self, execution_date):
        queued = self.delayed_rebalance
        if queued is None:
            return
        if queued["remaining"] > 0:
            queued["remaining"] -= 1
            return
        reason = queued.get("reason")
        delay_note = f"EXECUTION_DELAY_FROM_{queued['signal_date'].date()}"
        reason = f"{reason}|{delay_note}" if reason else delay_note
        self.portfolio.start_rebalance(
            queued["target"], queued["days"], date=execution_date,
            reason=reason,
        )
        self.delayed_rebalance = None


    # ==================================================
    # ETF 데이터 로딩
    # ==================================================
    def load_one(self, ticker):
        file = self.data_dir / f"{ticker}.csv"
        df = pd.read_csv(
            file,
            index_col="Date",
            parse_dates=True
        )
        required = {
            "ROC5",
            "ROC20",
            "ROC40",
            "ROC60",
            "EMA20_SLOPE5",
            "EMA200_SLOPE20",
            "DRAWDOWN120",
            "RSI14",
        }
        if not required.issubset(df.columns):
            df = Indicator.add_indicators(df)
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
    def get_prices(self, row, field="Close"):
        prices = {}
        for ticker in self.tickers:
            column = f"{ticker}_{field}"
            prices[ticker] = row.get(column, row[f"{ticker}_Close"])
        return prices

 
    # ==================================================
    # 전략 입력 데이터
    # ==================================================
    def get_market(self, row):
        market = {}
        for ticker in self.tickers:
            market[ticker] = {
                "Close": row[f"{ticker}_Close"],
                "EMA20": row.get(f"{ticker}_EMA20"),
                "EMA55": row.get(f"{ticker}_EMA55"),
                "EMA200": row.get(f"{ticker}_EMA200"),
                "ROC5": row.get(f"{ticker}_ROC5"),
                "ROC20": row.get(f"{ticker}_ROC20"),
                "ROC40": row.get(f"{ticker}_ROC40"),
                "ROC60": row.get(f"{ticker}_ROC60"),
                "EMA20_SLOPE5": row.get(f"{ticker}_EMA20_SLOPE5"),
                "EMA200_SLOPE20": row.get(f"{ticker}_EMA200_SLOPE20"),
                "DRAWDOWN120": row.get(f"{ticker}_DRAWDOWN120"),
                "RSI14": row.get(f"{ticker}_RSI14"),
            }
        return market    
    

    # ==================================================
    # 백테스트 실행
    # ==================================================
    def run(self):

        first_signal = True

        for date, row in self.data.iterrows():
            # A signal observed at yesterday's close is executed at today's open.
            open_prices = self.get_prices(row, field="Open")
            self._activate_delayed_rebalance(date)
            self.portfolio.update(open_prices, date=date)

            prices = self.get_prices(row, field="Close")
            market = self.get_market(row)
            signal  = self.strategy.evaluate(date, market, self.portfolio)

            # ------------------------------------------
            # 최초 투자
            # ------------------------------------------
            if first_signal:
                self.portfolio.start_rebalance(
                    signal["target"], days=signal["days"], date=date,
                    reason=signal.get("reason"),
                )
                first_signal = False

            # ------------------------------------------
            # 전략에 따른 리밸런싱
            # ------------------------------------------
            elif signal["rebalance"]:
                self._queue_rebalance(signal, date)

            # ------------------------------------------
            # 일별 기록
            # ------------------------------------------
            state = getattr(self.strategy, "state", None)
            if hasattr(state, "value"):
                state = state.value
            self.portfolio.record(
                date,
                prices,
                metadata={
                    "StrategyState": state,
                    "RiskOffScore": getattr(self.strategy, "risk_off_score", None),
                    "RecoveryScore": getattr(self.strategy, "recovery_score", None),
                    "SafeAsset": getattr(self.strategy, "safe_asset", None),
                },
            )

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
