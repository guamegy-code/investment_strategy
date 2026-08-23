"""Shared market-indicator definitions for desktop and web research charts."""

INDICATORS = {
    "Price": ["Close"],
    "MA": ["MA20", "MA55", "MA120", "MA200"],
    "EMA": ["EMA20", "EMA55", "EMA120", "EMA200"],
    "RSI": ["RSI14"],
    "Disparity": ["DISPARITY60"],
    "MACD": ["MACD", "MACD_SIGNAL", "MACD_HIST"],
    "Stochastic": ["STOCH_K", "STOCH_D"],
    "ROC": ["ROC252"],
    "TR": ["TR"],
    "ATR": ["ATR", "ATR60"],
    "Bollinger": ["BB_UPPER", "BB_MIDDLE", "BB_LOWER"],
    "Volatility": ["VOL60"],
    "MDD": ["MDD252"],
}

INDEXED_INDICATORS = {"Price", "MA", "EMA", "Bollinger"}
PANEL_BY_INDICATOR = {
    **{name: "price" for name in ("Price", "MA", "EMA", "Bollinger")},
    **{name: "oscillator" for name in ("RSI", "Disparity", "MACD", "Stochastic")},
    **{name: "risk" for name in ("ROC", "TR", "ATR", "Volatility", "MDD")},
}
DETAIL_ROWS = {"MA", "EMA", "Bollinger", "MACD", "Stochastic", "ATR"}
PANEL_ORDER = ("price", "oscillator", "risk")
PANEL_LABELS = {"price": "가격·추세", "oscillator": "오실레이터", "risk": "리스크"}

INDICATOR_LABELS = {
    "Close": "종가",
    "DISPARITY60": "60일 이격도",
    "MACD_SIGNAL": "MACD Signal",
    "MACD_HIST": "MACD Histogram",
    "STOCH_K": "Stochastic %K",
    "STOCH_D": "Stochastic %D",
    "ROC252": "ROC 252일",
    "ATR60": "ATR 60일",
    "BB_UPPER": "볼린저 상단",
    "BB_MIDDLE": "볼린저 중앙",
    "BB_LOWER": "볼린저 하단",
    "VOL60": "변동성 60일",
    "MDD252": "MDD 252일",
}


def indicator_panel(column: str) -> str | None:
    """Return the display panel for an indicator data column."""
    for indicator, columns in INDICATORS.items():
        if column in columns:
            return PANEL_BY_INDICATOR[indicator]
    return None


def indicator_is_indexed(column: str) -> bool:
    """Whether a column should share the selected period's price baseline."""
    return any(
        column in columns and indicator in INDEXED_INDICATORS
        for indicator, columns in INDICATORS.items()
    )


def indicator_options(panel: str) -> list[dict[str, str]]:
    """Return Dash-compatible options in the same order as the desktop chart."""
    return [
        {"label": INDICATOR_LABELS.get(column, column), "value": column}
        for indicator, columns in INDICATORS.items()
        if PANEL_BY_INDICATOR[indicator] == panel
        for column in columns
    ]
