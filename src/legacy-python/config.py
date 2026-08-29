"""Shared paths and runtime assumptions for the backtest."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
EXTENDED_DATA_DIR = PROJECT_ROOT / "data_extended"
RESULT_DIR = PROJECT_ROOT / "results"

for directory in (DATA_DIR, EXTENDED_DATA_DIR, RESULT_DIR):
    directory.mkdir(exist_ok=True)

TICKERS = ["QQQ", "BND", "GLD", "BIL", "QLD", "SPY"]
FX_RATE_TICKERS = ["KRW=X"]
GENERAL_COMPARISON_START_DATE = "2012-01-03"
# Backward-compatible alias used by the production strategy runner.
START_DATE = GENERAL_COMPARISON_START_DATE
# 지표 계산용 선행 데이터. 실제 백테스트는 START_DATE부터 시작한다.
DATA_START_DATE = "2010-01-01"
EXTENDED_START_DATE = "1999-03-10"
END_DATE = None

COMMISSION = 0.00015
SLIPPAGE = 0.00020

RISK_FREE_RATE = 0.03
TRADING_DAYS = 252

FIGURE_SIZE = (15, 8)
SAVE_FIGURE = True
SHOW_CHART = True
