"""
portfolio.py
"""

from copy import deepcopy


class Portfolio:

    def __init__(self, commission=0.0, slippage=0.0):
        # All backtests use an indexed starting value of 1.0.
        self.cash = 1.0
        self.positions = {}
        self.history = []
        self.trades = []
        self.rebalances = []
        self.commission = commission
        self.slippage = slippage
        self.total_costs = 0.0

        # -----------------------------
        # 분할 리밸런싱
        # -----------------------------
        self.pending_target = None
        self.remaining_days = 0
        self.total_days = 0

    # ==================================================
    # 현재 평가금액
    # ==================================================
    def value(self, prices):
        value = self.cash
        for ticker, shares in self.positions.items():
            value += shares * prices[ticker]
        return value

    # ==================================================
    # 현재 비중
    # ==================================================
    def weights(self, prices):
        total = self.value(prices)
        weights = {}
        for ticker in prices:
            amount = self.positions.get(ticker, 0) * prices[ticker]
            weights[ticker] = amount / total
        return weights
    

    def is_same_target(self, target, tol=1e-6):
        if self.pending_target is None:
            return False
        for ticker in target:
            if abs(self.pending_target[ticker] - target[ticker]) > tol:
                return False
        return True

    # ==================================================
    # 분할 리밸런싱 시작
    # ==================================================
    def start_rebalance(self, target, days=5, date=None, reason=None):
        self.pending_target = deepcopy(target)
        self.remaining_days = days
        self.total_days = days
        self.rebalances.append({
            "Date": date,
            "Target": deepcopy(target),
            "Reason": reason,
        })

    # ==================================================
    # 분할 리밸런싱 실행
    # ==================================================
    def update(self, prices, date=None):
        if self.pending_target is None:
            return
        current = self.weights(prices)
        total = self.value(prices)

        target = {}
        for ticker in self.pending_target:
            now = current.get(ticker, 0)
            goal = self.pending_target[ticker]
            diff = goal - now
            target[ticker] = now + diff / self.remaining_days

        self.rebalance(prices, target, date=date)
        self.remaining_days -= 1
        if self.remaining_days == 0:
            self.pending_target = None
            self.total_days = 0

    # ==================================================
    # 목표 비중 리밸런싱
    # ==================================================
    def rebalance(self, prices, target, date=None):
        total = self.value(prices)

        # -----------------------------
        # 매도 먼저
        # -----------------------------
        for ticker, weight in target.items():
            price = prices[ticker]
            target_value = total * weight
            current_value = self.positions.get(ticker, 0) * price
            diff = target_value - current_value

            if diff < 0:
                shares = abs(diff) / price
                self.trade(ticker, -shares, price, date=date)

        # -----------------------------
        # 매수
        # -----------------------------
        for ticker, weight in target.items():
            price = prices[ticker]
            target_value = total * weight
            current_value = self.positions.get(ticker, 0) * price
            diff = target_value - current_value

            if diff > 0:
                shares = diff / price
                self.trade(ticker, shares, price, date=date)

    # ==================================================
    # 매매
    # ==================================================
    def trade(self, ticker, shares, price, date=None):
        if abs(shares) < 1e-8:
            return

        direction = 1 if shares > 0 else -1
        execution_price = price * (1 + direction * self.slippage)

        # Do not borrow cash merely to pay transaction costs.
        if shares > 0:
            affordable = max(self.cash, 0.0) / (
                execution_price * (1 + self.commission)
            )
            shares = min(shares, affordable)
            if shares < 1e-8:
                return

        notional = shares * execution_price
        fee = abs(notional) * self.commission
        self.cash -= notional + fee
        self.total_costs += fee + abs(shares) * abs(execution_price - price)
        self.positions[ticker] = self.positions.get(ticker, 0) + shares
        self.trades.append({
            "Date": date,
            "Ticker": ticker,
            "Shares": shares,
            "Price": execution_price,
            "ReferencePrice": price,
            "Fee": fee,
            "Cash": self.cash,
        })


    # ==================================================
    # 일별 기록
    # ==================================================
    def record(self, date, prices, metadata=None):
        row = {
            "Date": date,
            "Portfolio": self.value(prices),
            "Cash": self.cash,
            "TransactionCosts": self.total_costs,
            "Weights": self.weights(prices).copy(),
            "Positions": self.positions.copy(),
        }
        if metadata:
            row.update(metadata)
        self.history.append(row)

    # ==================================================
    # 결과 반환
    # ==================================================
    def get_history(self):
        return self.history


    def get_trades(self):
        return self.trades


    def get_rebalances(self):
        return self.rebalances
