"""
portfolio.py
"""

from copy import deepcopy


class Portfolio:

    def __init__(self):
        # All backtests use an indexed starting value of 1.0.
        self.cash = 1.0
        self.positions = {}
        self.history = []
        self.trades = []
        self.rebalances = []

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
    def start_rebalance(self, target, days=5, date=None):
        self.pending_target = deepcopy(target)
        self.remaining_days = days
        self.total_days = days
        self.rebalances.append({
            "Date": date,
            "Target": deepcopy(target),
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
        cost = shares * price
        self.cash -= cost
        self.positions[ticker] = self.positions.get(ticker, 0) + shares
        self.trades.append({
            "Date": date,
            "Ticker": ticker,
            "Shares": shares,
            "Price": price,
            "Cash": self.cash,
        })


    # ==================================================
    # 일별 기록
    # ==================================================
    def record(self, date, prices):
        self.history.append({
            "Date": date,
            "Portfolio": self.value(prices),
            "Cash": self.cash,
            "Weights": self.weights(prices).copy(),
            "Positions": self.positions.copy(),
        })

    # ==================================================
    # 결과 반환
    # ==================================================
    def get_history(self):
        return self.history


    def get_trades(self):
        return self.trades


    def get_rebalances(self):
        return self.rebalances
