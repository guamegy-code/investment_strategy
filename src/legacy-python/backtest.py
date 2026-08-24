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
from strategy_domain import MarketSnapshot, StrategyEngine, StrategyEvaluation


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
        start_date=None,
        end_date=None,
    ):

        self.strategy = strategy
        self.data_dir = data_dir
        self.tickers = list(tickers or TICKERS)
        self.signal_delay_days = signal_delay_days
        self.bear_signal_delay_days = bear_signal_delay_days
        self.start_date = (
            pd.Timestamp(start_date) if start_date is not None else None
        )
        self.end_date = (
            pd.Timestamp(end_date) if end_date is not None else None
        )
        self.delayed_rebalance = None
        self.data = self.load_data()
        self.portfolio = Portfolio(
            commission=commission,
            slippage=slippage,
        )
        self.strategy_engine = (
            StrategyEngine(strategy)
            if callable(getattr(strategy, "evaluate", None))
            else None
        )
        self.last_evaluation = None

    def _delay_for_signal(self):
        state = getattr(self.strategy, "state", None)
        if hasattr(state, "value"):
            state = state.value
        if state == "BEAR" and self.bear_signal_delay_days is not None:
            return self.bear_signal_delay_days
        return self.signal_delay_days

    def _queue_rebalance(self, signal, signal_date):
        delay = self._delay_for_signal()
        if isinstance(signal, StrategyEvaluation):
            target = signal.target_weights
            days = signal.execution_days
            reason = signal.reason
        else:
            target = signal["target"]
            days = signal["days"]
            reason = signal.get("reason")
        if delay <= 0:
            # An immediately actionable new signal invalidates any older order
            # that was still waiting for its execution date.
            self.delayed_rebalance = None
            self.portfolio.start_rebalance(
                target, days, date=signal_date, reason=reason,
            )
            return
        # A later signal supersedes an order that has not started executing.
        self.delayed_rebalance = {
            "remaining": delay,
            "target": target.copy(),
            "days": days,
            "signal_date": signal_date,
            "reason": reason,
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
            "ROC120",
            "ROC252",
            "EMA20_SLOPE5",
            "EMA200_SLOPE20",
            "DRAWDOWN120",
            "RSI14",
            "DISPARITY60",
            "VOL60",
        }
        strategy_fields = {
            field.upper()
            for field in getattr(
                self.strategy, "required_market_fields", {}
            ).get(ticker, ())
        }
        available = {str(column).upper() for column in df.columns}
        if not required.issubset(df.columns) or not strategy_fields.issubset(available):
            df = Indicator.add_indicators(df)
            available = {str(column).upper() for column in df.columns}
        missing = sorted(strategy_fields - available)
        if missing:
            raise ValueError(
                f"Strategy requires unavailable indicators for {ticker}: "
                + ", ".join(missing)
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
        if self.start_date is not None:
            merged = merged.loc[merged.index >= self.start_date]
        if self.end_date is not None:
            merged = merged.loc[merged.index <= self.end_date]
        required_fields = getattr(
            self.strategy, "required_market_fields", {}
        )
        columns_by_name = {
            str(column).casefold(): column for column in merged.columns
        }
        readiness_columns = []
        for ticker, fields in required_fields.items():
            for field in fields:
                expected = f"{ticker}_{field}".casefold()
                column = columns_by_name.get(expected)
                if column is None:
                    raise ValueError(
                        f"Strategy requires unavailable market field: {ticker}.{field}"
                    )
                readiness_columns.append(column)
        if readiness_columns:
            merged = merged.dropna(subset=readiness_columns)
        if merged.empty:
            raise ValueError(
                "No overlapping market data in the configured backtest period"
            )
        return merged


    # ==================================================
    # 현재 가격
    # ==================================================
    def get_prices(self, row, field="Close"):
        prices = {}
        holding_tickers = getattr(self.strategy, "holding_tickers", self.tickers)
        for ticker in holding_tickers:
            column = f"{ticker}_{field}"
            prices[ticker] = row.get(column, row[f"{ticker}_Close"])
        return prices

 
    # ==================================================
    # 전략 입력 데이터
    # ==================================================
    def get_market(self, row):
        market = {}
        required_fields = getattr(
            self.strategy, "required_market_fields", {}
        )
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
                "ROC120": row.get(f"{ticker}_ROC120"),
                "ROC252": row.get(f"{ticker}_ROC252"),
                "EMA20_SLOPE5": row.get(f"{ticker}_EMA20_SLOPE5"),
                "EMA200_SLOPE20": row.get(f"{ticker}_EMA200_SLOPE20"),
                "DRAWDOWN120": row.get(f"{ticker}_DRAWDOWN120"),
                "RSI14": row.get(f"{ticker}_RSI14"),
                "DISPARITY60": row.get(f"{ticker}_DISPARITY60"),
                "VOL60": row.get(f"{ticker}_VOL60"),
            }
            for field in required_fields.get(ticker, ()):
                if any(
                    key.casefold() == field.casefold()
                    for key in market[ticker]
                ):
                    continue
                source_column = next(
                    (
                        key for key in row
                        if key.casefold() == f"{ticker}_{field}".casefold()
                    ),
                    None,
                )
                market[ticker][field] = (
                    row.get(source_column) if source_column is not None else None
                )
        return market    
    

    # ==================================================
    # 백테스트 실행
    # ==================================================
    def run(self):

        first_signal = True
        columns = self.data.columns
        if self.strategy_engine is None:
            self.strategy_engine = StrategyEngine(self.strategy)

        # ``iterrows`` creates a pandas Series for every trading day and each
        # lookup then pays pandas indexing overhead.  Strategies only need a
        # mapping, so tuples plus one plain dict per row are substantially
        # cheaper while preserving the exact input values.
        for values in self.data.itertuples(index=True, name=None):
            date = values[0]
            row = dict(zip(columns, values[1:]))
            # A signal observed at yesterday's close is executed at today's open.
            open_prices = self.get_prices(row, field="Open")
            self._activate_delayed_rebalance(date)
            self.portfolio.update(open_prices, date=date)

            prices = self.get_prices(row, field="Close")
            market = self.get_market(row)
            step = self.strategy_engine.advance(
                MarketSnapshot(date, market), self.portfolio
            )
            signal = step.evaluation
            self.last_evaluation = signal

            # ------------------------------------------
            # 최초 투자
            # ------------------------------------------
            if first_signal:
                self.portfolio.start_rebalance(
                    signal.target_weights,
                    days=signal.execution_days,
                    date=date,
                    reason=signal.reason,
                )
                first_signal = False

            # ------------------------------------------
            # 전략에 따른 리밸런싱
            # ------------------------------------------
            elif signal.rebalance_required:
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
