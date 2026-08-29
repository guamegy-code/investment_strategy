"""Interactive investment-strategy chart and controls."""

import json
from dataclasses import dataclass
from unicodedata import east_asian_width

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.path import Path
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import NullFormatter, NullLocator
from matplotlib.widgets import CheckButtons, TextBox
import numpy as np
import pandas as pd

from config import DATA_DIR, FIGURE_SIZE, RESULT_DIR, SAVE_FIGURE, SHOW_CHART, TICKERS
from indicator_catalog import (
    DETAIL_ROWS,
    INDEXED_INDICATORS,
    INDICATORS,
    PANEL_BY_INDICATOR,
    PANEL_ORDER,
)
from strategy_domain import strategy_display_name
APPLE_COLORS = ["#007AFF", "#FF9500", "#34C759", "#AF52DE", "#FF2D55", "#5AC8FA"]
LINE_STYLES = ["-", "--", ":", "-."]
ROW_LINE_STYLES = {"MACD": "-", "MACD_SIGNAL": "--"}
SELECTION_FILE = RESULT_DIR / "chart_selection.json"
CONTROL_FONT_SIZE = 11
CONTROL_TITLE_X = 0.0
CONTROL_TITLE_Y = 1.0
CONTROL_TITLE_BOX_GAP_INCHES = 0.1
CHECKBOX_INCHES = 0.13
CHECKBOX_SELECTED_MARK = "✓"
CHECKBOX_CHECK_PATH = Path(
    [(-0.50, -0.05), (-0.10, -0.45), (0.55, 0.50)],
    [Path.MOVETO, Path.LINETO, Path.LINETO],
)
CONTROL_WIDTH_INCHES = 4.2
CONTROL_RIGHT_MARGIN_INCHES = 0.3
STRATEGY_SECTION_TOP_INCHES = 0.63
MATRIX_SECTION_GAP_INCHES = 0.4
BUTTON_HEIGHT_INCHES = 0.3
CONTROL_ROW_SPACING_INCHES = 0.3
CONTROL_BOX_PADDING_INCHES = 0.33
MATRIX_TICKER_HEADER_HEIGHT_INCHES = 0.3
BUTTON_GAP_INCHES = 0.2
DATE_INPUT_BOTTOM_INCHES = 0.2
DATE_INPUT_VERTICAL_PADDING_INCHES = 0.1
DATE_INPUT_HEIGHT_INCHES = CONTROL_FONT_SIZE / 72 + DATE_INPUT_VERTICAL_PADDING_INCHES
DATE_INPUT_WIDTH_INCHES = 1.1
DATE_INPUT_LABEL_PAD = 0.08
SELECTOR_GROUP_GAP_INCHES = MATRIX_SECTION_GAP_INCHES
MATRIX_LINE_SAMPLE_WIDTH_INCHES = 0.42
MATRIX_LINE_TO_CHECKBOX_GAP_INCHES = 0.3
MATRIX_LABEL_X = 0.03
# Reserve enough room for long indicator titles before the line sample.
MATRIX_FIRST_COLUMN_X = 0.48
MATRIX_COLUMNS_WIDTH = 0.47
CHART_LEFT = 0.05
CHART_BOTTOM = 0.07
CHART_TOP = 0.93
CHART_TO_CONTROL_GAP = 0.04
PANEL_GAP = 0.025
PANEL_HEIGHT_WEIGHTS = {"price": 2.2, "oscillator": 1.4, "risk": 1.4}
MONTH_GRID_MAX_YEARS = 3
MONTH_GRID_MIN_WIDTH_INCHES = 5.0
TIMEFRAME_STYLES = {
    "daily": {"rule": None, "width": 0.7},
    "weekly": {"rule": "W-FRI", "width": 4.0},
    "monthly": {"rule": "ME", "width": 10.0},
}
TIMEFRAME_MAX_DAYS = {"daily": 365.25, "weekly": 3 * 365.25, "monthly": 10 * 365.25}
CANDLE_UP_COLOR = "#FF3B30"
CANDLE_DOWN_COLOR = "#007AFF"
REGIME_COLORS = {
    "BULL": "#34C759",
    "CAUTION": "#FFCC00",
    "BEAR": "#FF3B30",
    "RECOVERY": "#5AC8FA",
}
ALLOCATION_DISPLAY_ORDER = ("QQQ", "BND", "GLD", "BIL", "QLD", "TQQQ")
DEFAULT_RISK_ASSETS = ("QQQ", "QLD", "TQQQ")
RISK_WEIGHT_TOLERANCE = 1e-6



@dataclass
class ChartSelection:
    strategies: dict
    state_colors: dict
    fx_neutral: dict
    matrix: dict
    visible_rows: list
    visible_tickers: list


def configure_fonts():
    """Use Consolas for Latin glyphs and Malgun Gothic as the Korean fallback."""
    plt.rcParams["font.family"] = ["Consolas", "Malgun Gothic"]
    plt.rcParams["axes.unicode_minus"] = False


configure_fonts()


def build_matrix_rows():
    """Map visible matrix rows to their source indicator and column."""
    rows = {}
    column_rows = {}
    for indicator, columns in INDICATORS.items():
        if indicator in DETAIL_ROWS:
            for column in columns:
                rows[column] = (indicator, column)
                column_rows[(indicator, column)] = column
        else:
            rows[indicator] = (indicator, None)
            for column in columns:
                column_rows[(indicator, column)] = indicator
    return rows, column_rows


MATRIX_ROWS, ROW_FOR_COLUMN = build_matrix_rows()
INDICATOR_STYLE_INDEX = {indicator: index for index, indicator in enumerate(INDICATORS)}
PANEL_TITLES = {"tickers": "종목", "price": "가격", "oscillator": "오실레이터", "risk": "리스크"}
SELECTOR_COLUMNS = 4
SELECTOR_ROWS_BY_PANEL = {
    panel: [
        [row for row, (source, _) in MATRIX_ROWS.items() if source == indicator]
        for indicator in INDICATORS
        if PANEL_BY_INDICATOR[indicator] == panel
    ]
    for panel in PANEL_ORDER
}


def load_selection():
    if not SELECTION_FILE.exists():
        return {}
    try:
        with SELECTION_FILE.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_selection(selection):
    with SELECTION_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "strategies": selection.strategies,
                "state_colors": selection.state_colors,
                "fx_neutral": selection.fx_neutral,
                "matrix": selection.matrix,
                "visible_rows": selection.visible_rows,
                "visible_tickers": selection.visible_tickers,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )


def strategy_color(index):
    return APPLE_COLORS[index % len(APPLE_COLORS)]


def strategy_control_label(name):
    """오른쪽 제어판에서는 중복되는 Strategy 접미사를 생략한다."""
    return name[:-len("Strategy")] if name.endswith("Strategy") else name


def ticker_color(index, strategy_count):
    return APPLE_COLORS[(index + strategy_count) % len(APPLE_COLORS)]


def ticker_chart_color(ticker, visible_tickers, strategy_count):
    """Return the color used by both a visible ticker label and its lines."""
    return ticker_color(list(visible_tickers).index(ticker), strategy_count)


def indicator_line_style(row):
    if row in ROW_LINE_STYLES:
        return ROW_LINE_STYLES[row]
    indicator, _ = MATRIX_ROWS[row]
    return LINE_STYLES[INDICATOR_STYLE_INDEX[indicator] % len(LINE_STYLES)]


def selected_indicators_for_panel(panel, selection):
    return {
        MATRIX_ROWS[row][0]
        for row in selection.visible_rows
        if PANEL_BY_INDICATOR[MATRIX_ROWS[row][0]] == panel
        and any(selection.matrix[row].get(ticker, False) for ticker in selection.visible_tickers)
    }


def displayed_indicator_line_style(row, selection):
    if row in ROW_LINE_STYLES:
        return ROW_LINE_STYLES[row]
    indicator, _ = MATRIX_ROWS[row]
    panel = PANEL_BY_INDICATOR[indicator]
    selected_indicators = selected_indicators_for_panel(panel, selection)
    if panel in {"oscillator", "risk"} and len(selected_indicators) == 1:
        return "-"
    return indicator_line_style(row)


def build_selection(results, market_data, saved):
    saved_strategies = saved.get("strategies", {}) if isinstance(saved.get("strategies"), dict) else {}
    saved_state_colors = saved.get("state_colors", {}) if isinstance(saved.get("state_colors"), dict) else {}
    saved_fx_neutral = saved.get("fx_neutral", {}) if isinstance(saved.get("fx_neutral"), dict) else {}
    saved_matrix = saved.get("matrix", {}) if isinstance(saved.get("matrix"), dict) else {}
    saved_tickers = saved.get("tickers", {}) if isinstance(saved.get("tickers"), dict) else {}
    saved_indicators = saved.get("indicators", {}) if isinstance(saved.get("indicators"), dict) else {}
    matrix_rows = list(MATRIX_ROWS)
    has_visible_rows = isinstance(saved.get("visible_rows"), list)
    has_visible_tickers = isinstance(saved.get("visible_tickers"), list)
    saved_matrix_tickers = {
        ticker
        for row in saved_matrix.values()
        if isinstance(row, dict)
        for ticker in row
    }

    strategies = {
        strategy_display_name(result["strategy"]): bool(saved_strategies.get(strategy_display_name(result["strategy"]), True))
        for result in results
    }
    state_colors = {
        strategy_display_name(result["strategy"]): bool(
            saved_state_colors.get(strategy_display_name(result["strategy"]), False)
        )
        for result in results
    }
    fx_neutral = {
        strategy_display_name(result["strategy"]): bool(
            saved_fx_neutral.get(strategy_display_name(result["strategy"]), False)
        )
        for result in results
    }
    matrix = {
        row: {
            ticker: bool(
                saved_matrix.get(row, {}).get(
                    ticker,
                    saved_matrix.get(indicator, {}).get(
                        ticker,
                        bool(saved_tickers.get(ticker, True)) and bool(
                            saved_indicators.get(
                                indicator,
                                indicator == "Price"
                                or (indicator == "Disparity" and ticker == "QQQ"),
                            )
                        ),
                    ),
                )
            )
            for ticker in market_data
        }
        for row, (indicator, _) in MATRIX_ROWS.items()
    }
    visible_rows = [row for row in matrix_rows if row in saved["visible_rows"]] if has_visible_rows else matrix_rows
    visible_tickers = (
        [
            ticker for ticker in market_data
            if ticker in saved["visible_tickers"]
            or ticker not in saved_matrix_tickers
        ]
        if has_visible_tickers
        else [ticker for ticker in market_data if saved_tickers.get(ticker, True)]
    )
    return ChartSelection(
        strategies,
        state_colors,
        fx_neutral,
        matrix,
        visible_rows,
        visible_tickers,
    )


def style_axes(fig, axes):
    fig.patch.set_facecolor("#F5F5F7")
    for axis in axes:
        axis.set_facecolor("#FFFFFF")
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#E5E5EA", linewidth=0.8)
        axis.grid(axis="x", color="#E5E5EA", linewidth=0.8)
        axis.xaxis.set_major_locator(mdates.YearLocator())
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        axis.tick_params(colors="#6E6E73", labelsize=9, length=0, pad=7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color("#D2D2D7")
        axis.spines["bottom"].set_color("#D2D2D7")


def style_control_axis(axis, title):
    axis.set_facecolor("#FFFFFF")
    title_text = axis.set_title(
        title,
        fontsize=CONTROL_FONT_SIZE,
        loc="left",
        color="#1D1D1F",
        fontweight="bold",
        pad=CONTROL_TITLE_BOX_GAP_INCHES * 72,
    )
    title_text.set_x(CONTROL_TITLE_X)
    title_text.set_y(CONTROL_TITLE_Y)
    axis.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in axis.spines.values():
        spine.set_color("#E5E5EA")


def chart_tickers(results):
    """Add alternative risk assets used by active strategies to the chart."""
    tickers = list(TICKERS)
    for result in results:
        strategy = result.get("strategy")
        alternative = getattr(strategy, "ALTERNATIVE_RISK_ASSET", None)
        if alternative:
            tickers.append(alternative)
    return tuple(dict.fromkeys(tickers))


def load_market_data(tickers=None):
    market_data = {}
    for ticker in tickers or TICKERS:
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"Chart skipped for {ticker}: {path} not found")
            continue
        data = pd.read_csv(path, index_col="Date", parse_dates=True)
        if "Close" in data:
            if "DISPARITY60" not in data:
                data["DISPARITY60"] = data["Close"] / data["Close"].rolling(60).mean() * 100
            market_data[ticker] = data
    return market_data


def load_fx_rate_series(results, supplied=None):
    """전략별 환율 제거 계산에 사용할 환율 종가 시계열을 반환한다."""
    rate_tickers = tuple(dict.fromkeys(
        ticker
        for result in results
        if (ticker := getattr(result.get("strategy"), "FX_RATE_TICKER", None))
    ))
    source = supplied
    if source is None:
        source = load_market_data(rate_tickers) if rate_tickers else {}
    rates = {}
    for ticker in rate_tickers:
        data = source.get(ticker) if isinstance(source, dict) else None
        if isinstance(data, pd.DataFrame) and "Close" in data:
            rates[ticker] = data["Close"]
        elif isinstance(data, pd.Series):
            rates[ticker] = data
    return rates


def index_to_start(series):
    valid = series.dropna()
    return series if valid.empty else series / valid.iloc[0]


def series_from_start(series, start_date, normalize=False, base_series=None):
    if start_date is not None:
        series = series.loc[series.index >= start_date]
        if base_series is not None:
            base_series = base_series.loc[base_series.index >= start_date]
    if base_series is not None:
        valid_base = base_series.dropna()
        return series if valid_base.empty else series / valid_base.iloc[0]
    return index_to_start(series) if normalize else series


def fx_neutralize_series(series, fx_rates, start_date=None):
    """현재 환율을 시작 시점 환율로 고정한 가격 또는 자산가치를 계산한다.

    ``fx_rates``는 1달러당 원화처럼 원화/외화 형식이어야 한다. 따라서
    원화 표시 값에 ``시작 환율 / 현재 환율``을 곱하면 환율 변동분이
    제거된다. 서로 다른 휴장일은 직전 환율을 사용한다.
    """
    if series.empty or fx_rates is None or fx_rates.empty:
        return series.copy()
    combined_index = series.index.union(fx_rates.index).sort_values()
    aligned_rates = (
        fx_rates.reindex(combined_index).ffill().reindex(series.index)
    )
    eligible = pd.concat(
        [series.rename("value"), aligned_rates.rename("fx")], axis=1
    )
    if start_date is not None:
        eligible = eligible.loc[eligible.index >= start_date]
    eligible = eligible.dropna()
    if eligible.empty or eligible["fx"].iloc[0] <= 0:
        return series.copy()
    start_rate = eligible["fx"].iloc[0]
    valid_rates = aligned_rates.where(aligned_rates > 0)
    return series * start_rate / valid_rates


def fx_neutralize_portfolio(result, fx_rates, start_date=None):
    """환노출 상품의 평가액만 시작 시점 환율로 다시 계산한다."""
    history = result.get("history")
    market_data = result.get("market_data")
    strategy = result.get("strategy")
    if history is None or history.empty or "Portfolio" not in history:
        return pd.Series(dtype=float)
    portfolio = history["Portfolio"].copy()
    if (
        "Positions" not in history
        or market_data is None
        or market_data.empty
    ):
        return portfolio

    combined_index = portfolio.index.union(fx_rates.index).sort_values()
    aligned_rates = (
        fx_rates.reindex(combined_index).ffill().reindex(portfolio.index)
    )
    eligible_rates = aligned_rates
    if start_date is not None:
        eligible_rates = eligible_rates.loc[eligible_rates.index >= start_date]
    eligible_rates = eligible_rates.dropna()
    if eligible_rates.empty or eligible_rates.iloc[0] <= 0:
        return portfolio
    start_rate = eligible_rates.iloc[0]
    rate_multiplier = start_rate / aligned_rates.where(aligned_rates > 0) - 1.0

    exposed_tickers = getattr(strategy, "fx_exposed_tickers", None)
    if exposed_tickers is None:
        exposed_tickers = strategy_risk_assets(strategy)
    exposed_value = pd.Series(0.0, index=portfolio.index)
    for ticker in exposed_tickers:
        price_column = f"{ticker}_Close"
        if price_column not in market_data:
            continue
        shares = history["Positions"].map(
            lambda positions: float((positions or {}).get(ticker, 0.0))
        )
        prices = market_data[price_column].reindex(portfolio.index)
        exposed_value = exposed_value.add(shares * prices, fill_value=0.0)
    return portfolio + exposed_value * rate_multiplier


def strategy_risk_assets(strategy):
    """Return the traded assets that make up a strategy's risk sleeve."""
    explicit = getattr(strategy, "risk_asset_tickers", None)
    if explicit:
        return tuple(explicit)
    configured = getattr(strategy, "risk_assets", None)
    if isinstance(configured, dict) and configured:
        return tuple(configured)
    configured = getattr(strategy, "RISK_ASSETS", None)
    if configured:
        return tuple(configured)
    configured = getattr(strategy, "RISK_ASSET", None)
    if configured:
        return (configured,)
    return DEFAULT_RISK_ASSETS


def rebalance_marker_events(history, trades, rebalances, risk_assets=("QQQ",)):
    """Match each rebalance signal with its first execution and target.

    Rebalance signals are recorded at day t's close and the engine executes
    them from day t+1's open. Matching trades to the signal date therefore
    hides valid markers; each event is paired with its first later trade date.
    """
    if trades is None or trades.empty or "Date" not in trades:
        return {}
    trades = trades.dropna(subset=["Date"]).copy()
    trades["Date"] = pd.to_datetime(trades["Date"])
    matched_events = {}
    if rebalances:
        ordered_rebalances = sorted(
            (event for event in rebalances if event.get("Date") is not None),
            key=lambda event: pd.Timestamp(event["Date"]),
        )
        trade_dates = trades["Date"].drop_duplicates().sort_values()
        execution_events = []
        for index, rebalance in enumerate(ordered_rebalances):
            recorded_execution = rebalance.get("ExecutionDate")
            if recorded_execution is not None:
                execution_events.append((pd.Timestamp(recorded_execution), rebalance))
                continue
            signal_date = pd.Timestamp(rebalance["Date"])
            next_signal = (
                pd.Timestamp(ordered_rebalances[index + 1]["Date"])
                if index + 1 < len(ordered_rebalances)
                else None
            )
            candidates = trade_dates[trade_dates > signal_date]
            if next_signal is not None:
                candidates = candidates[candidates <= next_signal]
            if not candidates.empty:
                execution_events.append((candidates.iloc[0], rebalance))
    else:
        execution_events = [
            (date, {"Date": date, "Target": history.at[date, "Weights"]})
            for date in trades["Date"].drop_duplicates()
            if date in history.index
        ]

    for execution_date, rebalance in execution_events:
        if execution_date not in history.index:
            continue
        day_trades = trades[trades["Date"] == execution_date]
        weights = history.at[execution_date, "Weights"]
        if day_trades.empty or not isinstance(weights, dict):
            continue
        target = rebalance.get("Target", {})
        pre_weights = rebalance.get(
            "PreWeights",
            history.at[pd.Timestamp(rebalance["Date"]), "Weights"]
            if pd.Timestamp(rebalance["Date"]) in history.index
            else {},
        )
        risk_assets = tuple(risk_assets)
        before_risk = sum(
            float(pre_weights.get(ticker, 0.0)) for ticker in risk_assets
        )
        target_risk = sum(
            float(target.get(ticker, 0.0)) for ticker in risk_assets
        )
        risk_change = target_risk - before_risk
        direction = (
            "up"
            if risk_change > RISK_WEIGHT_TOLERANCE
            else "down"
            if risk_change < -RISK_WEIGHT_TOLERANCE
            else "same"
        )
        matched_events[execution_date] = {
            "direction": direction,
            "signal_date": pd.Timestamp(rebalance["Date"]),
            "target": target,
            "execution_days": rebalance.get("ExecutionDays", 1),
            "pre_weights": pre_weights,
            "risk_assets": risk_assets,
            "before_risk": before_risk,
            "target_risk": target_risk,
            "reason": rebalance.get("Reason"),
        }
    return matched_events


def rebalance_directions(history, trades, rebalances, risk_assets=("QQQ",)):
    """Return a risk-sleeve up/down/same marker for each rebalance."""
    return {
        date: event["direction"]
        for date, event in rebalance_marker_events(
            history, trades, rebalances, risk_assets=risk_assets
        ).items()
    }


def _display_width(value):
    return sum(
        2 if east_asian_width(char) in {"W", "F"} else 1
        for char in value
    )


def _pad_display(value, width):
    return value + " " * max(width - _display_width(value), 0)


def _align_right_display(value, width):
    return " " * max(width - _display_width(value), 0) + value


def format_rebalance_table(pre_weights, target, risk_assets=()):
    """Format aligned asset rows with risk assets displayed first."""
    if not isinstance(target, dict) or not target:
        return "목표 비중 정보 없음"
    if not isinstance(pre_weights, dict):
        pre_weights = {}
    risk_assets = tuple(risk_assets)
    tickers = [ticker for ticker in risk_assets if ticker in target]
    tickers.extend(
        ticker
        for ticker in ALLOCATION_DISPLAY_ORDER
        if ticker in target and ticker not in tickers
    )
    tickers.extend(sorted(ticker for ticker in target if ticker not in tickers))
    asset_header = "종목"
    before_header = "이전(%)"
    target_header = "목표(%)"
    asset_width = max(_display_width(asset_header), *map(_display_width, tickers))
    before_width = max(_display_width(before_header), 7)
    target_width = max(_display_width(target_header), 7)
    header = (
        f"{_pad_display(asset_header, asset_width)}  "
        f"{_align_right_display(before_header, before_width)}  "
        f"{_align_right_display(target_header, target_width)}"
    )
    rows = [header, "-" * _display_width(header)]
    for ticker in tickers:
        before = float(pre_weights.get(ticker, 0.0)) * 100
        goal = float(target[ticker]) * 100
        rows.append(
            f"{_pad_display(ticker, asset_width)}  "
            f"{before:>{before_width}.1f}  {goal:>{target_width}.1f}"
        )
    return "\n".join(rows)


def nearest_chart_date(clicked_date, series_by_name):
    """Return the nearest date available in the displayed data area."""
    available = pd.DatetimeIndex([])
    for series in series_by_name.values():
        if series is None or series.empty:
            continue
        available = available.union(pd.DatetimeIndex(series.dropna().index))
    if available.empty:
        return None
    available = available.sort_values().unique()
    clicked_date = pd.Timestamp(clicked_date)
    if clicked_date.tzinfo is not None:
        clicked_date = clicked_date.tz_localize(None)
    position = available.get_indexer([clicked_date], method="nearest")[0]
    return pd.Timestamp(available[position])


def chart_values_on_date(series_by_name, date):
    """Collect finite values that exist on the selected chart date."""
    values = {}
    for name, series in series_by_name.items():
        if date not in series.index:
            continue
        value = series.loc[date]
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        if pd.notna(value):
            values[name] = float(value)
    return values



def format_chart_value_popup(date, strategy_values, price_values, weight_values=None):
    """Format strategy value, price levels, and current weights using aligned name columns."""
    rows = [f"날짜: {pd.Timestamp(date):%Y-%m-%d}"]
    all_names = (
        *strategy_values, 
        *price_values, 
        *(weight_values if weight_values else ())
    )
    name_width = max(map(_display_width, all_names), default=0)
    
    if strategy_values:
        rows.extend(("", "전략"))
        rows.extend(
            f"{_pad_display(name, name_width)}  {value:,.4f}"
            for name, value in strategy_values.items()
        )
    if price_values:
        rows.extend(("", "가격(시작일=1)"))
        rows.extend(
            f"{_pad_display(name, name_width)}  {value:,.4f}"
            for name, value in price_values.items()
        )
    if weight_values:
        rows.extend(("", "현재 비중"))
        rows.extend(
            f"{_pad_display(name, name_width)}  {weight * 100:6.2f}%"
            for name, weight in weight_values.items()
        )

    return "\n".join(rows)


def fit_annotation_inside_axis(annotation, axis, figure, padding=8):
    """Shift an annotation until its text box stays inside the chart panel."""
    renderer = figure.canvas.get_renderer()
    annotation_box = annotation.get_window_extent(renderer=renderer)
    axis_box = axis.get_window_extent(renderer=renderer)
    left = axis_box.x0 + padding
    right = axis_box.x1 - padding
    bottom = axis_box.y0 + padding
    top = axis_box.y1 - padding

    if annotation_box.width > right - left:
        shift_x = left - annotation_box.x0
    elif annotation_box.x1 > right:
        shift_x = right - annotation_box.x1
    elif annotation_box.x0 < left:
        shift_x = left - annotation_box.x0
    else:
        shift_x = 0

    if annotation_box.height > top - bottom:
        shift_y = bottom - annotation_box.y0
    elif annotation_box.y1 > top:
        shift_y = top - annotation_box.y1
    elif annotation_box.y0 < bottom:
        shift_y = bottom - annotation_box.y0
    else:
        shift_y = 0

    offset_x, offset_y = annotation.get_position()
    pixels_to_points = 72 / figure.dpi
    annotation.set_position((
        offset_x + shift_x * pixels_to_points,
        offset_y + shift_y * pixels_to_points,
    ))


def set_date_guide(line, date=None):
    """Move a vertical popup guide to ``date`` or hide it when absent."""
    if date is None:
        line.set_visible(False)
        return
    x_value = mdates.date2num(pd.Timestamp(date))
    line.set_xdata((x_value, x_value))
    line.set_visible(True)


def strategy_state_series(history):
    """Return chartable strategy states, excluding missing or unknown values."""
    if history is None or history.empty or "StrategyState" not in history:
        return pd.Series(dtype="object")
    states = history["StrategyState"]
    return states.where(states.isin(REGIME_COLORS))


def state_line_segments(values, states):
    """Build adjacent portfolio-line segments colored by the starting state."""
    frame = pd.concat(
        [values.rename("value"), states.reindex(values.index).rename("state")],
        axis=1,
    ).dropna(subset=["value"])
    if len(frame) < 2:
        return np.empty((0, 2, 2)), []

    points = np.column_stack((
        mdates.date2num(frame.index.to_numpy()),
        frame["value"].to_numpy(),
    ))
    starting_states = frame["state"].to_numpy()[:-1]
    valid = np.isin(starting_states, tuple(REGIME_COLORS))
    segments = np.stack((points[:-1], points[1:]), axis=1)[valid]
    colors = [REGIME_COLORS[state] for state in starting_states[valid]]
    return segments, colors


def draw_strategy_lines(price_axis, results, strategy_visibility, state_color_selection):
    strategy_lines = {}
    strategy_state_lines = {}
    strategy_markers = {}
    strategy_series = {}
    strategy_states = {}
    state_color_available = {}
    marker_dates = {}
    visible_marker_dates = {}
    strategy_rebalance_events = {}
    for index, result in enumerate(results):
        name = strategy_display_name(result["strategy"])
        history = result["history"]
        strategy_series[name] = history["Portfolio"]
        strategy_states[name] = strategy_state_series(history)
        state_color_available[name] = strategy_states[name].notna().any()
        values = index_to_start(strategy_series[name])
        state_color_enabled = (
            state_color_available[name] and state_color_selection[name]
        )
        (line,) = price_axis.plot(
            values.index,
            values,
            color=strategy_color(index),
            linewidth=1.2,
            solid_capstyle="round",
            visible=strategy_visibility[name],
            alpha=0.0 if state_color_enabled else 1.0,
        )
        strategy_lines[name] = line
        segments, colors = state_line_segments(values, strategy_states[name])
        state_line = LineCollection(
            segments,
            colors=colors,
            linewidths=1.8,
            capstyle="round",
            zorder=2.5,
            visible=strategy_visibility[name] and state_color_enabled,
        )
        price_axis.add_collection(state_line)
        strategy_state_lines[name] = state_line

        markers = []
        dates_by_marker = []
        visible_dates_by_marker = []
        strategy_rebalance_events[name] = rebalance_marker_events(
            history,
            result["trades"],
            result.get("rebalances", []),
            risk_assets=strategy_risk_assets(result["strategy"]),
        )
        for direction, marker in (("up", "^"), ("down", "v"), ("same", "o")):
            dates = [
                date
                for date, event in strategy_rebalance_events[name].items()
                if event["direction"] == direction
            ]
            points = values.reindex(dates).dropna()
            if not points.empty:

   # [추가] 방향별 마커 색상 정의 (한국 스타일: 매수/확대=빨강, 매도/축소=파랑)
                marker_colors = {
                    "up": "#FF3B30",   # 애플 스타일 레드
                    "down": "#007AFF", # 애플 스타일 블루
                    "same": "#1D1D1F" # "#8E8E93"  # 회색
                }             
                markers.append(price_axis.scatter(
                    points.index, points.values, marker=marker, s=60,
                    color=marker_colors[direction], edgecolors="white", linewidths=0.7,
                    zorder=3, visible=strategy_visibility[name], picker=5,
                ))
                dates_by_marker.append(dates)
                visible_dates_by_marker.append(list(points.index))
        strategy_markers[name] = markers
        marker_dates[name] = dates_by_marker
        visible_marker_dates[name] = visible_dates_by_marker
    return (
        strategy_lines,
        strategy_state_lines,
        strategy_markers,
        strategy_series,
        strategy_states,
        state_color_available,
        marker_dates,
        visible_marker_dates,
        strategy_rebalance_events,
    )


def draw_indicator_lines(panel_axes, market_data, chart_start, strategy_count, selection):
    panel_lines = {"price": [], "oscillator": [], "risk": []}
    indicator_lines = {}
    indicator_series = {}
    for ticker, data in market_data.items():
        if chart_start is not None:
            data = data.loc[data.index >= chart_start]
        color_tickers = (
            selection.visible_tickers
            if ticker in selection.visible_tickers
            else market_data
        )
        color = ticker_chart_color(ticker, color_tickers, strategy_count)
        for indicator, columns in INDICATORS.items():
            axis = panel_axes[PANEL_BY_INDICATOR[indicator]]
            for column in columns:
                if column not in data or data[column].dropna().empty:
                    continue
                base_series = data["Close"] if indicator in INDEXED_INDICATORS else None
                values = series_from_start(data[column], chart_start, base_series=base_series)
                row = ROW_FOR_COLUMN[(indicator, column)]
                (line,) = axis.plot(
                    values.index, values, color=color,
                    linestyle=displayed_indicator_line_style(row, selection),
                    linewidth=0.8, alpha=0.8, solid_capstyle="round",
                    visible=selection.matrix[row][ticker],
                )
                indicator_lines[(row, ticker, column)] = line
                indicator_series[(row, ticker, column)] = (data[column], base_series)
                panel_lines[PANEL_BY_INDICATOR[indicator]].append(line)
    return panel_lines, indicator_lines, indicator_series


def draw_timeframe_candles(price_axis, market_data, chart_start):
    """Create daily, weekly, and monthly candles for zoom-dependent display."""
    candle_lines = []
    candle_series = []
    candle_lines_by_ticker = {}
    for ticker, data in market_data.items():
        candle_lines_by_ticker[ticker] = {timeframe: [] for timeframe in TIMEFRAME_STYLES}
        ohlc = data[["Open", "High", "Low", "Close"]].dropna()
        if chart_start is not None:
            ohlc = ohlc.loc[ohlc.index >= chart_start]
        if ohlc.empty:
            continue
        # Use the same baseline as the Price line: the first close at chart start.
        ohlc = ohlc / ohlc["Close"].iloc[0]
        for timeframe, style in TIMEFRAME_STYLES.items():
            if style["rule"] is None:
                candles = ohlc
            else:
                candles = ohlc.resample(style["rule"]).agg(
                    Open=("Open", "first"), High=("High", "max"),
                    Low=("Low", "min"), Close=("Close", "last"),
                ).dropna()
            if candles.empty:
                continue
            date_numbers = mdates.date2num(candles.index.to_numpy())
            opens = candles["Open"].to_numpy()
            highs = candles["High"].to_numpy()
            lows = candles["Low"].to_numpy()
            closes = candles["Close"].to_numpy()
            body_bottoms = np.minimum(opens, closes)
            body_tops = body_bottoms + np.maximum(np.abs(closes - opens), 1e-10)
            half_width = style["width"] / 2
            colors = np.where(
                closes >= opens, CANDLE_UP_COLOR, CANDLE_DOWN_COLOR
            )
            wick_segments = np.stack((
                np.column_stack((date_numbers, lows)),
                np.column_stack((date_numbers, highs)),
            ), axis=1)
            body_vertices = np.stack((
                np.column_stack((date_numbers - half_width, body_bottoms)),
                np.column_stack((date_numbers - half_width, body_tops)),
                np.column_stack((date_numbers + half_width, body_tops)),
                np.column_stack((date_numbers + half_width, body_bottoms)),
            ), axis=1)
            wicks = LineCollection(wick_segments, colors=colors, linewidths=0.8,
                                   alpha=0.75, zorder=2, visible=False)
            bodies = PolyCollection(body_vertices, facecolors=colors, edgecolors=colors,
                                    alpha=0.45, zorder=2, visible=False)
            price_axis.add_collection(wicks)
            price_axis.add_collection(bodies)
            candle_lines.extend([wicks, bodies])
            candle_lines_by_ticker[ticker][timeframe].extend([wicks, bodies])
            candle_series.append(candles["Close"])
    return candle_lines, candle_series, candle_lines_by_ticker



def draw_chart(
    results,
    price_data=None,
    show_chart=SHOW_CHART,
    fx_rate_data=None,
):
    """Draw strategy performance and a ticker-by-indicator selection matrix."""
    market_data = (
        price_data
        if price_data is not None
        else load_market_data(chart_tickers(results))
    )
    selection = build_selection(results, market_data, load_selection())
    fx_rates = load_fx_rate_series(results, supplied=fx_rate_data)
    results_by_name = {
        strategy_display_name(result["strategy"]): result for result in results
    }
    strategies_by_name = {
        name: result["strategy"] for name, result in results_by_name.items()
    }
    strategy_names = list(selection.strategies)
    matrix_rows = list(MATRIX_ROWS)
    figure_height = 9
    control_left = 1 - (CONTROL_RIGHT_MARGIN_INCHES + CONTROL_WIDTH_INCHES) / FIGURE_SIZE[0]
    fig, axes = plt.subplots(
        3,
        1,
        sharex=True,
        figsize=(FIGURE_SIZE[0], figure_height),
        gridspec_kw={"height_ratios": [2.2, 1.4, 1.4], "hspace": 0.08},
    )
    price_axis, oscillator_axis, risk_axis = axes
    panel_axes = {"price": price_axis, "oscillator": oscillator_axis, "risk": risk_axis}
    state_legend = fig.legend(
        handles=[Patch(facecolor=color, edgecolor="none", label=state)
                 for state, color in REGIME_COLORS.items()],
        loc="upper left",
        bbox_to_anchor=(CHART_LEFT, 0.99),
        ncol=4,
        frameon=False,
        fontsize=9,
        handlelength=1.2,
        columnspacing=1.2,
    )
    state_legend.set_visible(False)
    if fig.canvas.manager is not None:
        fig.canvas.manager.set_window_title("투자 전략")
    style_axes(fig, axes)

    default_start_date = min(
        (result["history"].index[0] for result in results if not result["history"].empty),
        default=None,
    )
    active_start_date = default_start_date
    (
        strategy_lines,
        strategy_state_lines,
        strategy_markers,
        strategy_series,
        strategy_states,
        state_color_available,
        marker_dates,
        visible_marker_dates,
        strategy_rebalance_events,
    ) = draw_strategy_lines(
        price_axis, results, selection.strategies, selection.state_colors
    )
    rebalance_annotation = price_axis.annotate(
        "",
        xy=(0, 0),
        xytext=(14, 18),
        textcoords="offset points",
        ha="left",
        va="bottom",
        multialignment="left",
        fontsize=9,
        fontfamily=["Consolas", "Malgun Gothic"],
        color="#1D1D1F",
        bbox={
            "boxstyle": "round,pad=0.45",
            "facecolor": "white",
            "edgecolor": "#D2D2D7",
            "alpha": 0.96,
        },
        arrowprops={"arrowstyle": "->", "color": "#6E6E73", "linewidth": 0.8},
        zorder=10,
        visible=False,
    )
    value_annotation = price_axis.annotate(
        "",
        xy=(0, 0),
        xytext=(14, 18),
        textcoords="offset points",
        ha="left",
        va="bottom",
        multialignment="left",
        fontsize=9,
        fontfamily=["Consolas", "Malgun Gothic"],
        color="#1D1D1F",
        bbox={
            "boxstyle": "round,pad=0.45",
            "facecolor": "white",
            "edgecolor": "#007AFF",
            "alpha": 0.96,
        },
        arrowprops={"arrowstyle": "->", "color": "#007AFF", "linewidth": 0.8},
        zorder=10,
        visible=False,
    )
    value_date_guide = price_axis.axvline(
        0,
        color="#007AFF",
        linewidth=0.8,
        linestyle="--",
        alpha=0.65,
        zorder=8,
        visible=False,
    )
    panel_lines, indicator_lines, indicator_series = draw_indicator_lines(
        panel_axes, market_data, active_start_date, len(results), selection
    )
    timeframe_lines, timeframe_series, timeframe_lines_by_ticker = draw_timeframe_candles(
        price_axis, market_data, active_start_date
    )
    panel_lines["price"].extend(strategy_lines.values())
    panel_lines["price"].extend(timeframe_lines)
    chart_series = [*strategy_series.values(), *(series for series, _ in indicator_series.values()), *timeframe_series]
    chart_end_date = max(
        (series.index.max() for series in chart_series if not series.empty),
        default=None,
    )
    is_clamping_x_limits = False
    zoom_drag_start = None

    def rescale():
        for panel, axis in panel_axes.items():
            if any(line.get_visible() for line in panel_lines[panel]):
                axis.relim(visible_only=True)
                axis.autoscale_view(scalex=False, scaley=True)

    def update_panels():
        visible = [panel for panel, lines in panel_lines.items() if any(line.get_visible() for line in lines)]
        left, right, bottom, top = CHART_LEFT, control_left - CHART_TO_CONTROL_GAP, CHART_BOTTOM, CHART_TOP
        if not visible:
            for axis in axes:
                axis.set_visible(False)
                axis.set_navigate(False)
            return
        available = top - bottom - PANEL_GAP * (len(visible) - 1)
        current_top = top
        for panel, axis in panel_axes.items():
            if panel not in visible:
                axis.set_visible(False)
                axis.set_navigate(False)
                continue
            height = available * PANEL_HEIGHT_WEIGHTS[panel] / sum(PANEL_HEIGHT_WEIGHTS[item] for item in visible)
            axis.set_visible(True)
            axis.set_navigate(True)
            axis.set_position([left, current_top - height, right - left, height])
            current_top -= height + PANEL_GAP
        for axis in axes:
            axis.tick_params(labelbottom=axis in (panel_axes[panel] for panel in visible))

    def update_month_grid(_=None):
        left, right = price_axis.get_xlim()
        displayed_days = abs(right - left)
        figure_width, _ = fig.get_size_inches()
        chart_width = price_axis.get_position().width * figure_width
        show_month_grid = (
            displayed_days <= 365.25 * MONTH_GRID_MAX_YEARS
            and chart_width >= MONTH_GRID_MIN_WIDTH_INCHES
        )
        for axis in axes:
            if show_month_grid:
                # January is already covered by the major yearly gridline.
                axis.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=range(2, 13)))
                axis.xaxis.set_minor_formatter(NullFormatter())
                axis.grid(True, which="minor", axis="x", color="#E5E5EA", linewidth=0.6)
            else:
                axis.xaxis.set_minor_locator(NullLocator())
                axis.grid(False, which="minor", axis="x")

    def persist():
        selection.strategies = {name: line.get_visible() for name, line in strategy_lines.items()}
        save_selection(selection)

    def displayed_strategy_series(name):
        """현재 시작일과 환율 제거 선택을 반영한 전략 수익곡선을 반환한다."""
        series = strategy_series[name]
        strategy = strategies_by_name[name]
        rate_ticker = getattr(strategy, "FX_RATE_TICKER", None)
        if selection.fx_neutral[name] and rate_ticker in fx_rates:
            series = fx_neutralize_portfolio(
                results_by_name[name], fx_rates[rate_ticker], active_start_date
            )
        return series_from_start(series, active_start_date, normalize=True)

    def refresh_state_legend():
        state_legend.set_visible(any(
            strategy_lines[name].get_visible()
            and state_color_available[name]
            and selection.state_colors[name]
            for name in strategy_names
        ))

    def refresh_lines():
        rebalance_annotation.set_visible(False)
        value_annotation.set_visible(False)
        set_date_guide(value_date_guide)
        for name, line in strategy_lines.items():
            values = displayed_strategy_series(name)
            line.set_data(values.index, values)
            state_enabled = state_color_available[name] and selection.state_colors[name]
            line.set_alpha(0.0 if state_enabled else 1.0)
            segments, colors = state_line_segments(values, strategy_states[name])
            strategy_state_lines[name].set_segments(segments)
            strategy_state_lines[name].set_color(colors)
            strategy_state_lines[name].set_visible(line.get_visible() and state_enabled)
            for marker_index, (marker, dates) in enumerate(zip(
                strategy_markers[name], marker_dates[name]
            )):
                points = values.reindex(dates).dropna()
                marker.set_offsets(np.column_stack((marker.axes.convert_xunits(points.index), points.values)))
                visible_marker_dates[name][marker_index] = list(points.index)
        for (row, ticker, _), line in indicator_lines.items():
            series, base_series = indicator_series[(row, ticker, _)]
            values = series_from_start(series, active_start_date, base_series=base_series)
            line.set_data(values.index, values)
            line.set_visible(
                row in selection.visible_rows
                and ticker in selection.visible_tickers
                and selection.matrix[row][ticker]
            )
            line.set_linestyle(displayed_indicator_line_style(row, selection))
        update_panels()
        rescale()
        refresh_state_legend()

    def refresh_indicator_display():
        """Update checkbox-driven visibility without recalculating chart data."""
        for (row, ticker, _), line in indicator_lines.items():
            line.set_visible(
                row in selection.visible_rows
                and ticker in selection.visible_tickers
                and selection.matrix[row][ticker]
            )
            if ticker in selection.visible_tickers:
                line.set_color(ticker_chart_color(
                    ticker, selection.visible_tickers, len(results)
                ))
            line.set_linestyle(displayed_indicator_line_style(row, selection))
        update_panels()

    def on_rebalance_marker_click(event):
        if event.button != 1 or event.inaxes is not price_axis:
            return
        for name in strategy_names:
            if not strategy_lines[name].get_visible():
                continue
            for marker_index, marker in enumerate(strategy_markers[name]):
                contains, details = marker.contains(event)
                indices = details.get("ind", [])
                if not contains or len(indices) == 0:
                    continue
                point_index = int(indices[0])
                dates = visible_marker_dates[name][marker_index]
                if point_index >= len(dates):
                    continue
                execution_date = pd.Timestamp(dates[point_index])
                marker_event = strategy_rebalance_events[name].get(execution_date)
                if marker_event is None:
                    continue
                event.chart_popup_handled = True
                value_annotation.set_visible(False)
                set_date_guide(value_date_guide)
                offsets = marker.get_offsets()
                rebalance_annotation.xy = tuple(offsets[point_index])
                axis_box = price_axis.get_window_extent()
                place_left = event.x > (axis_box.x0 + axis_box.x1) / 2
                place_below = event.y > (axis_box.y0 + axis_box.y1) / 2
                rebalance_annotation.set_position((
                    -14 if place_left else 14,
                    -18 if place_below else 18,
                ))
                rebalance_annotation.set_ha("right" if place_left else "left")
                rebalance_annotation.set_va("top" if place_below else "bottom")
                # The horizontal alignment anchors the box on either side of
                # the marker; multiline content itself must remain left-aligned.
                rebalance_annotation.set_multialignment("left")
                allocation_table = format_rebalance_table(
                    marker_event["pre_weights"],
                    marker_event["target"],
                    marker_event["risk_assets"],
                )
                rebalance_annotation.set_text(
                    f"체결일: {execution_date:%Y-%m-%d}\n"
                    f"분할 체결 기간: {marker_event['execution_days']}거래일\n\n"
                    f"{allocation_table}"
                )
                rebalance_annotation.set_visible(True)
                # Render once to measure the real text box, then clamp it to
                # the plot panel so it never covers the controls on the right.
                fig.canvas.draw()
                fit_annotation_inside_axis(
                    rebalance_annotation, price_axis, fig
                )
                fig.canvas.draw_idle()
                return
        if rebalance_annotation.get_visible():
            rebalance_annotation.set_visible(False)
            fig.canvas.draw_idle()

    def on_chart_value_click(event):
        if event.button == 3:
            was_visible = (
                rebalance_annotation.get_visible()
                or value_annotation.get_visible()
            )
            rebalance_annotation.set_visible(False)
            value_annotation.set_visible(False)
            set_date_guide(value_date_guide)
            if was_visible:
                fig.canvas.draw_idle()
            return
        if (
            event.button != 1
            or event.inaxes is not price_axis
            or event.xdata is None
            or event.ydata is None
            or getattr(event, "chart_popup_handled", False)
            or toolbar_navigation_is_active()
        ):
            return

        displayed_strategies = {
            name: displayed_strategy_series(name)
            for name, line in strategy_lines.items()
            if line.get_visible()
        }
        displayed_prices = {
            ticker: series_from_start(
                data["Close"], active_start_date, normalize=True
            )
            for ticker, data in market_data.items()
            if (
                "Price" in selection.visible_rows
                and ticker in selection.visible_tickers
                and selection.matrix["Price"].get(ticker, False)
                and "Close" in data
            )
        }
        all_displayed = {
            **{f"strategy:{name}": series for name, series in displayed_strategies.items()},
            **{f"price:{ticker}": series for ticker, series in displayed_prices.items()},
        }
        clicked_date = pd.Timestamp(mdates.num2date(event.xdata))
        selected_date = nearest_chart_date(clicked_date, all_displayed)
        if selected_date is None:
            rebalance_annotation.set_visible(False)
            value_annotation.set_visible(False)
            set_date_guide(value_date_guide)
            fig.canvas.draw_idle()
            return

        strategy_values = chart_values_on_date(
            displayed_strategies, selected_date
        )
        price_values = chart_values_on_date(displayed_prices, selected_date)

        # ==========================================================
        # [추가됨] 화면에 표시 중인 전략들의 '현재 비중' 추출 로직
        # ==========================================================
        weight_values = {}
        visible_strategies = list(displayed_strategies.keys())
        
        for result in results:
            name = result["strategy"].__class__.__name__
            if name in visible_strategies:
                history = result["history"]
                if selected_date in history.index and "Weights" in history:
                    weights = history.at[selected_date, "Weights"]
                    if isinstance(weights, dict):
                        # 활성화된 전략이 2개 이상일 때는 헷갈리지 않게 종목명 앞에 [전략명]을 붙여줌
                        prefix = f"[{name}] " if len(visible_strategies) > 1 else ""
                        for ticker, weight in weights.items():
                            if float(weight) > 0.0001:  # 비중이 0인 종목은 깔끔하게 숨김
                                weight_values[f"{prefix}{ticker}"] = float(weight)
        # ==========================================================

        rebalance_annotation.set_visible(False)
        set_date_guide(value_date_guide, selected_date)
        value_annotation.xy = (mdates.date2num(selected_date), event.ydata)
        axis_box = price_axis.get_window_extent()
        place_left = event.x > (axis_box.x0 + axis_box.x1) / 2
        place_below = event.y > (axis_box.y0 + axis_box.y1) / 2
        value_annotation.set_position((
            -14 if place_left else 14,
            -18 if place_below else 18,
        ))
        value_annotation.set_ha("right" if place_left else "left")
        value_annotation.set_va("top" if place_below else "bottom")
        value_annotation.set_multialignment("left")
        
        # [수정됨] 팝업 포맷 함수에 weight_values 인자 추가 전달
        value_annotation.set_text(format_chart_value_popup(
            selected_date, strategy_values, price_values, weight_values
        ))
        
        value_annotation.set_visible(True)
        fig.canvas.draw()
        fit_annotation_inside_axis(value_annotation, price_axis, fig)
        fig.canvas.draw_idle()

    def refresh_timeframe_visibility(_=None):
        displayed_days = abs(price_axis.get_xlim()[1] - price_axis.get_xlim()[0])
        if displayed_days <= TIMEFRAME_MAX_DAYS["daily"]:
            active_timeframe = "daily"
        elif displayed_days <= TIMEFRAME_MAX_DAYS["weekly"]:
            active_timeframe = "weekly"
        elif displayed_days <= TIMEFRAME_MAX_DAYS["monthly"]:
            active_timeframe = "monthly"
        else:
            active_timeframe = None
        for ticker, timeframe_lines in timeframe_lines_by_ticker.items():
            ticker_visible = (
                ticker in selection.visible_tickers
                and selection.matrix["Price"].get(ticker, False)
            )
            for timeframe, lines in timeframe_lines.items():
                visible = ticker_visible and timeframe == active_timeframe
                for line in lines:
                    line.set_visible(visible)
        rescale()

    def rebuild_timeframe_candles():
        nonlocal timeframe_lines, timeframe_series, timeframe_lines_by_ticker
        for artist in timeframe_lines:
            artist.remove()
        panel_lines["price"] = [
            artist for artist in panel_lines["price"] if artist not in timeframe_lines
        ]
        timeframe_lines, timeframe_series, timeframe_lines_by_ticker = draw_timeframe_candles(
            price_axis, market_data, active_start_date
        )
        panel_lines["price"].extend(timeframe_lines)

    def on_start_date_submit(value):
        nonlocal active_start_date
        value = value.strip()
        if not value:
            active_start_date = default_start_date
        else:
            parsed_date = pd.to_datetime(value, errors="coerce")
            if pd.isna(parsed_date):
                return
            active_start_date = pd.Timestamp(parsed_date)
        rebuild_timeframe_candles()
        reset_chart_view()
        refresh_timeframe_visibility()
        fig.canvas.draw_idle()

    def reset_chart_view():
        """Discard navigation zoom/pan state and fit every panel to the active date range."""
        refresh_lines()
        for axis in axes:
            if axis.get_visible():
                axis.relim(visible_only=True)
                axis.autoscale(enable=True, axis="y")
        price_axis.relim(visible_only=True)
        price_axis.autoscale(enable=True, axis="x")
        if active_start_date is not None:
            price_axis.set_xlim(left=active_start_date)
        clamp_x_limits(price_axis)

    def clamp_x_limits(axis):
        nonlocal is_clamping_x_limits
        if is_clamping_x_limits or active_start_date is None or chart_end_date is None:
            return
        lower_limit = mdates.date2num(active_start_date)
        upper_limit = mdates.date2num(chart_end_date)
        left, right = axis.get_xlim()
        if left >= lower_limit and right <= upper_limit:
            return

        span = min(right - left, upper_limit - lower_limit)
        new_left = max(lower_limit, min(left, upper_limit - span))
        new_right = new_left + span
        is_clamping_x_limits = True
        try:
            axis.set_xlim(new_left, new_right)
        finally:
            is_clamping_x_limits = False

    def reset_y_limits():
        for axis in axes:
            if axis.get_visible():
                axis.relim(visible_only=True)
                axis.autoscale(enable=True, axis="y")

    def prevent_y_zoom_when_x_is_limited(event):
        if event.button != "down" or active_start_date is None or chart_end_date is None:
            return
        clamp_x_limits(price_axis)
        left, right = price_axis.get_xlim()
        lower_limit = mdates.date2num(active_start_date)
        upper_limit = mdates.date2num(chart_end_date)
        if abs(left - lower_limit) < 1e-9 and abs(right - upper_limit) < 1e-9:
            reset_y_limits()
            fig.canvas.draw_idle()

    def toolbar_navigation_mode():
        toolbar = getattr(fig.canvas.manager, "toolbar", None)
        return (
            str(getattr(toolbar, "mode", "")).lower()
            if toolbar is not None
            else ""
        )

    def toolbar_navigation_is_active():
        return bool(toolbar_navigation_mode())

    def toolbar_zoom_is_active():
        mode = toolbar_navigation_mode()
        # Matplotlib labels Pan mode as "pan/zoom".  It must not trigger the
        # custom rectangle-zoom handler, or a pan drag is applied twice.
        return "zoom" in mode and "pan" not in mode

    def on_zoom_press(event):
        nonlocal zoom_drag_start
        if event.button == 1 and event.inaxes in axes and toolbar_zoom_is_active():
            zoom_drag_start = (event.inaxes, event.x, event.y, event.inaxes.transData.frozen().inverted())

    def on_zoom_release(event):
        nonlocal zoom_drag_start
        if zoom_drag_start is None:
            return
        axis, start_x, start_y, data_transform = zoom_drag_start
        zoom_drag_start = None
        if event.button != 1 or not toolbar_zoom_is_active():
            return
        if abs(event.x - start_x) < 5 or abs(event.y - start_y) < 5:
            return
        x0, x1 = sorted((start_x, event.x))
        y0, y1 = sorted((start_y, event.y))
        bbox = axis.bbox
        x0, x1 = max(x0, bbox.x0), min(x1, bbox.x1)
        y0, y1 = max(y0, bbox.y0), min(y1, bbox.y1)
        data_start = data_transform.transform((x0, y0))
        data_end = data_transform.transform((x1, y1))
        axis.set_xlim(sorted((data_start[0], data_end[0])))
        axis.set_ylim(sorted((data_start[1], data_end[1])))
        fig.canvas.draw_idle()

    def add_checkbox(axis, x, y):
        figure_width, figure_height = fig.get_size_inches()
        bounds = axis.get_position()
        box_width = CHECKBOX_INCHES / (bounds.width * figure_width)
        box_height = CHECKBOX_INCHES / (bounds.height * figure_height)
        box = Rectangle((x - box_width / 2, y - box_height / 2), box_width, box_height, transform=axis.transAxes,
                        facecolor="#FFFFFF", edgecolor="#1D1D1F", linewidth=1.0)
        axis.add_patch(box)
        mark = axis.text(
            x,
            y,
            CHECKBOX_SELECTED_MARK,
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=CONTROL_FONT_SIZE + 1,
            fontweight="bold",
            color="#1D1D1F",
        )
        return box, mark

    def control_position(bottom, height, width=CONTROL_WIDTH_INCHES):
        figure_width, figure_height = fig.get_size_inches()
        left = (figure_width - CONTROL_RIGHT_MARGIN_INCHES - width) / figure_width
        return [left, bottom / figure_height, width / figure_width, height / figure_height]

    def strategy_panel_height():
        return 2 * CONTROL_BOX_PADDING_INCHES + max(len(strategy_names) - 1, 0) * CONTROL_ROW_SPACING_INCHES

    def matrix_panel_height(rows):
        natural_height = (
            2 * CONTROL_BOX_PADDING_INCHES
            + MATRIX_TICKER_HEADER_HEIGHT_INCHES
            + max(rows - 1, 0) * CONTROL_ROW_SPACING_INCHES
        )
        _, figure_height = fig.get_size_inches()
        date_input_top = DATE_INPUT_BOTTOM_INCHES + DATE_INPUT_HEIGHT_INCHES
        controls_bottom = date_input_top + BUTTON_GAP_INCHES
        available_height = (
            figure_height
            - matrix_section_top()
            - controls_bottom
            - BUTTON_GAP_INCHES
            - BUTTON_HEIGHT_INCHES
        )
        return min(natural_height, max(available_height, 0))

    def minimum_figure_height():
        return (
            matrix_section_top()
            + 2 * CONTROL_BOX_PADDING_INCHES
            + MATRIX_TICKER_HEADER_HEIGHT_INCHES
            + 2 * BUTTON_GAP_INCHES
            + BUTTON_HEIGHT_INCHES
            + DATE_INPUT_BOTTOM_INCHES
            + DATE_INPUT_HEIGHT_INCHES
        )

    def matrix_panel_bottom(rows):
        _, figure_height = fig.get_size_inches()
        return figure_height - matrix_section_top() - matrix_panel_height(rows)

    def matrix_section_top():
        return STRATEGY_SECTION_TOP_INCHES + strategy_panel_height() + MATRIX_SECTION_GAP_INCHES

    def button_bottom(rows):
        return matrix_panel_bottom(rows) - BUTTON_GAP_INCHES - BUTTON_HEIGHT_INCHES

    def selector_panel_bottom():
        return BUTTON_HEIGHT_INCHES + BUTTON_GAP_INCHES

    def selector_panel_height():
        _, figure_height = fig.get_size_inches()
        return (
            figure_height
            - STRATEGY_SECTION_TOP_INCHES
            - selector_panel_bottom()
        )

    def selector_group_top(group_index, group_layouts, selector_height):
        used_height = sum(height for _, height in group_layouts[:group_index])
        used_gaps = SELECTOR_GROUP_GAP_INCHES * group_index
        return 1 - (used_height + used_gaps) / selector_height

    # Strategy controls
    strategy_axis = fig.add_axes(control_position(
        figure_height - STRATEGY_SECTION_TOP_INCHES - strategy_panel_height(),
        strategy_panel_height(),
    ))
    style_control_axis(strategy_axis, "전략")
    strategy_controls = []
    fx_neutral_controls = []
    state_color_controls = []
    for index, name in enumerate(strategy_names):
        y = 1 - (CONTROL_BOX_PADDING_INCHES + index * CONTROL_ROW_SPACING_INCHES) / strategy_panel_height()
        box, mark = add_checkbox(strategy_axis, 0.08, y)
        strategy_axis.text(0.13, y, strategy_control_label(name), transform=strategy_axis.transAxes, va="center", fontsize=CONTROL_FONT_SIZE,
                           color=strategy_lines[name].get_color())
        strategy_controls.append((name, box, mark))
        rate_ticker = getattr(strategies_by_name[name], "FX_RATE_TICKER", None)
        if rate_ticker in fx_rates:
            fx_box, fx_mark = add_checkbox(strategy_axis, 0.78, y)
            fx_neutral_controls.append((name, fx_box, fx_mark))
        else:
            strategy_axis.text(
                0.78, y, "-", transform=strategy_axis.transAxes,
                ha="center", va="center", fontsize=CONTROL_FONT_SIZE,
                color="#AEAEB2",
            )
        if state_color_available[name]:
            state_box, state_mark = add_checkbox(strategy_axis, 0.94, y)
            state_color_controls.append((name, state_box, state_mark))
        else:
            strategy_axis.text(
                0.94, y, "-", transform=strategy_axis.transAxes,
                ha="center", va="center", fontsize=CONTROL_FONT_SIZE,
                color="#AEAEB2",
            )
    strategy_axis.text(
        0.78, 1.01, "환율제거", transform=strategy_axis.transAxes,
        ha="center", va="bottom", fontsize=CONTROL_FONT_SIZE - 2,
        color="#6E6E73",
    )
    strategy_axis.text(
        0.94, 1.01, "상태색", transform=strategy_axis.transAxes,
        ha="center", va="bottom", fontsize=CONTROL_FONT_SIZE - 2,
        color="#6E6E73",
    )

    def refresh_strategy_controls():
        for name, _, mark in strategy_controls:
            mark.set_visible(strategy_lines[name].get_visible())
        for name, _, mark in fx_neutral_controls:
            mark.set_visible(selection.fx_neutral[name])
        for name, _, mark in state_color_controls:
            mark.set_visible(selection.state_colors[name])

    def on_strategy_click(event):
        if event.inaxes is not strategy_axis:
            return
        for name, box, _ in strategy_controls:
            if box.contains(event)[0]:
                line = strategy_lines[name]
                line.set_visible(not line.get_visible())
                strategy_state_lines[name].set_visible(
                    line.get_visible()
                    and state_color_available[name]
                    and selection.state_colors[name]
                )
                for marker in strategy_markers[name]:
                    marker.set_visible(line.get_visible())
                refresh_strategy_controls()
                update_panels()
                rescale()
                refresh_state_legend()
                persist()
                fig.canvas.draw_idle()
                return
        for name, box, _ in fx_neutral_controls:
            if box.contains(event)[0]:
                selection.fx_neutral[name] = not selection.fx_neutral[name]
                refresh_strategy_controls()
                refresh_lines()
                persist()
                fig.canvas.draw_idle()
                return
        for name, box, _ in state_color_controls:
            if box.contains(event)[0]:
                selection.state_colors[name] = not selection.state_colors[name]
                strategy_lines[name].set_alpha(
                    0.0 if selection.state_colors[name] else 1.0
                )
                strategy_state_lines[name].set_visible(
                    strategy_lines[name].get_visible()
                    and selection.state_colors[name]
                )
                refresh_strategy_controls()
                refresh_state_legend()
                persist()
                fig.canvas.draw_idle()
                return

    # Ticker-by-indicator matrix controls
    matrix_axis = fig.add_axes(control_position(
        matrix_panel_bottom(len(selection.visible_rows)), matrix_panel_height(len(selection.visible_rows))
    ))
    matrix_controls = []
    matrix_style_samples = []
    change_button_axis = fig.add_axes(control_position(
        button_bottom(len(selection.visible_rows)), BUTTON_HEIGHT_INCHES
    ))
    style_control_axis(change_button_axis, "")
    change_button_axis.text(0.5, 0.5, "항목 변경", transform=change_button_axis.transAxes,
                            ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#1D1D1F")
    date_input_axis = fig.add_axes(control_position(
        DATE_INPUT_BOTTOM_INCHES, DATE_INPUT_HEIGHT_INCHES, DATE_INPUT_WIDTH_INCHES
    ))
    initial_date_text = default_start_date.strftime("%Y-%m-%d") if default_start_date is not None else ""
    date_input = TextBox(
        date_input_axis,
        "Start date",
        initial=initial_date_text,
        label_pad=DATE_INPUT_LABEL_PAD,
    )
    date_input.label.set_fontsize(CONTROL_FONT_SIZE)
    date_input.text_disp.set_fontsize(CONTROL_FONT_SIZE)
    # Matplotlib's TextBox registers a resize callback wrapped as a mouse event
    # handler, which raises AttributeError on ResizeEvent in the installed version.
    date_input_resize_cid = date_input._cids.pop()
    fig.canvas.mpl_disconnect(date_input_resize_cid)
    selector_axis = None
    selector_apply_axis = None
    selector_widgets = []
    matrix_scroll_offset = 0

    def matrix_row_capacity():
        row_space = matrix_panel_height(len(selection.visible_rows))
        row_space -= 2 * CONTROL_BOX_PADDING_INCHES + MATRIX_TICKER_HEADER_HEIGHT_INCHES
        return max(1, int(row_space / CONTROL_ROW_SPACING_INCHES) + 1)

    def draw_matrix():
        nonlocal matrix_scroll_offset
        matrix_axis.clear()
        style_control_axis(matrix_axis, "지표 × 종목")
        matrix_controls.clear()
        matrix_style_samples.clear()
        if not selection.visible_rows:
            matrix_axis.text(0.5, 0.5, "표시할 지표 항목이 없습니다.", transform=matrix_axis.transAxes,
                             ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#6E6E73")
            return
        matrix_tickers = [ticker for ticker in market_data if ticker in selection.visible_tickers]
        if not matrix_tickers:
            matrix_axis.text(0.5, 0.5, "표시할 종목이 없습니다.", transform=matrix_axis.transAxes,
                             ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#6E6E73")
            return
        row_capacity = matrix_row_capacity()
        max_scroll_offset = max(len(selection.visible_rows) - row_capacity, 0)
        matrix_scroll_offset = min(matrix_scroll_offset, max_scroll_offset)
        displayed_rows = selection.visible_rows[
            matrix_scroll_offset:matrix_scroll_offset + row_capacity
        ]
        column_step = MATRIX_COLUMNS_WIDTH / len(matrix_tickers)
        height = matrix_panel_height(len(selection.visible_rows))
        ticker_title_y = 1 - (
            CONTROL_BOX_PADDING_INCHES
            + MATRIX_TICKER_HEADER_HEIGHT_INCHES
            - CONTROL_ROW_SPACING_INCHES
        ) / height
        row_start = 1 - (CONTROL_BOX_PADDING_INCHES + MATRIX_TICKER_HEADER_HEIGHT_INCHES) / height
        row_step = CONTROL_ROW_SPACING_INCHES / height
        figure_width, _ = fig.get_size_inches()
        matrix_width_inches = matrix_axis.get_position().width * figure_width
        checkbox_width = CHECKBOX_INCHES / matrix_width_inches
        line_width = MATRIX_LINE_SAMPLE_WIDTH_INCHES / matrix_width_inches
        line_gap = MATRIX_LINE_TO_CHECKBOX_GAP_INCHES / matrix_width_inches
        line_end = MATRIX_FIRST_COLUMN_X - checkbox_width / 2 - line_gap
        line_start = line_end - line_width
        for index, ticker in enumerate(matrix_tickers):
            x = MATRIX_FIRST_COLUMN_X + index * column_step
            matrix_axis.text(x, ticker_title_y, ticker, transform=matrix_axis.transAxes, ha="center", va="center",
                             fontsize=CONTROL_FONT_SIZE,
                             color=ticker_chart_color(
                                 ticker, selection.visible_tickers, len(results)
                             ))
        for row_index, row in enumerate(displayed_rows):
            y = row_start - row_index * row_step
            matrix_axis.text(MATRIX_LABEL_X, y, row, transform=matrix_axis.transAxes, va="center",
                             fontsize=CONTROL_FONT_SIZE, color="#333333")
            (sample_line,) = matrix_axis.plot(
                [line_start, line_end],
                [y, y],
                transform=matrix_axis.transAxes,
                color="#333333",
                linestyle=displayed_indicator_line_style(row, selection),
                linewidth=1.4,
                solid_capstyle="round",
            )
            matrix_style_samples.append((row, sample_line))
            for ticker_index, ticker in enumerate(matrix_tickers):
                x = MATRIX_FIRST_COLUMN_X + ticker_index * column_step
                box, mark = add_checkbox(matrix_axis, x, y)
                matrix_controls.append((row, ticker, box, mark))
        if max_scroll_offset:
            matrix_axis.text(
                0.98,
                0.02,
                "휠로 항목 스크롤",
                transform=matrix_axis.transAxes,
                ha="right",
                va="bottom",
                fontsize=CONTROL_FONT_SIZE - 2,
                color="#6E6E73",
            )
        refresh_matrix_controls()

    def refresh_matrix_controls():
        for row, ticker, _, mark in matrix_controls:
            mark.set_visible(selection.matrix[row][ticker])
        for row, sample_line in matrix_style_samples:
            sample_line.set_linestyle(displayed_indicator_line_style(row, selection))

    def on_matrix_click(event):
        if event.inaxes is not matrix_axis:
            return
        for row, ticker, box, _ in matrix_controls:
            if box.contains(event)[0]:
                selection.matrix[row][ticker] = not selection.matrix[row][ticker]
                refresh_matrix_controls()
                refresh_indicator_display()
                refresh_timeframe_visibility()
                persist()
                fig.canvas.draw_idle()
                return

    def on_matrix_scroll(event):
        nonlocal matrix_scroll_offset
        if event.inaxes is not matrix_axis or len(selection.visible_rows) <= matrix_row_capacity():
            return
        direction = -1 if event.button == "up" else 1
        max_scroll_offset = len(selection.visible_rows) - matrix_row_capacity()
        new_offset = min(max(matrix_scroll_offset + direction, 0), max_scroll_offset)
        if new_offset == matrix_scroll_offset:
            return
        matrix_scroll_offset = new_offset
        draw_matrix()
        fig.canvas.draw_idle()

    def toggle_selector_selection(group, item):
        if group == "tickers":
            if item in selection.visible_tickers:
                selection.visible_tickers.remove(item)
            else:
                selection.visible_tickers[:] = [
                    ticker for ticker in market_data if ticker in {*selection.visible_tickers, item}
                ]
            refresh_timeframe_visibility()
            return
        if item in selection.visible_rows:
            selection.visible_rows.remove(item)
        else:
            selection.visible_rows[:] = [
                row for row in matrix_rows if row in {*selection.visible_rows, item}
            ]

    def draw_selector_groups():
        nonlocal selector_widgets
        for child_axis in selector_axis.child_axes[:]:
            child_axis.remove()
        selector_axis.clear()
        selector_axis.set_facecolor("#F5F5F7")
        selector_axis.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in selector_axis.spines.values():
            spine.set_visible(False)

        selector_groups = [("tickers", [list(market_data)[index:index + SELECTOR_COLUMNS] for index in range(0, len(market_data), SELECTOR_COLUMNS)])]
        selector_groups.extend(SELECTOR_ROWS_BY_PANEL.items())
        group_layouts = [
            (panel, rows, 2 * CONTROL_BOX_PADDING_INCHES + max(len(rows) - 1, 0) * CONTROL_ROW_SPACING_INCHES)
            for panel, rows in selector_groups
        ]
        layout_for_positions = [(rows, height) for _, rows, height in group_layouts]
        selector_height = selector_panel_height()
        selector_widgets = []
        for group_index, (panel, indicator_rows, height) in enumerate(group_layouts):
            current_top = selector_group_top(group_index, layout_for_positions, selector_height)
            height_fraction = height / selector_height
            group_axis = selector_axis.inset_axes([0.0, current_top - height_fraction, 1.0, height_fraction])
            style_control_axis(group_axis, PANEL_TITLES[panel])
            checkbox_height = CHECKBOX_INCHES / height
            for row_index, rows in enumerate(indicator_rows):
                column_width = 0.96 / SELECTOR_COLUMNS
                row_y = 1 - (CONTROL_BOX_PADDING_INCHES + row_index * CONTROL_ROW_SPACING_INCHES) / height
                for column_index, row in enumerate(rows):
                    checkbox_axis = group_axis.inset_axes([
                        0.02 + column_index * column_width,
                        row_y - checkbox_height / 2,
                        column_width - 0.04,
                        checkbox_height,
                    ])
                    checkbox_axis.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
                    for spine in checkbox_axis.spines.values():
                        spine.set_visible(False)
                    selected = (
                        row in selection.visible_tickers
                        if panel == "tickers"
                        else row in selection.visible_rows
                    )
                    widget = CheckButtons(
                        checkbox_axis,
                        [row],
                        [selected],
                        check_props={"paths": [CHECKBOX_CHECK_PATH]},
                    )
                    for label in widget.labels:
                        label.set_fontsize(CONTROL_FONT_SIZE)
                    widget.on_clicked(lambda item, group=panel: toggle_selector_selection(group, item))
                    selector_widgets.append(widget)

    def open_row_selector(_):
        nonlocal selector_axis, selector_apply_axis, selector_widgets
        if selector_axis is not None:
            return
        strategy_axis.set_visible(False)
        matrix_axis.set_visible(False)
        change_button_axis.set_visible(False)
        date_input_axis.set_visible(False)
        selector_axis = fig.add_axes(control_position(selector_panel_bottom(), selector_panel_height()))
        draw_selector_groups()
        selector_apply_axis = fig.add_axes(control_position(BUTTON_GAP_INCHES, BUTTON_HEIGHT_INCHES))
        style_control_axis(selector_apply_axis, "")
        selector_apply_axis.text(0.5, 0.5, "적용", transform=selector_apply_axis.transAxes,
                                 ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#1D1D1F")
        fig.canvas.draw()

    def apply_selection():
        nonlocal selector_axis, selector_apply_axis, selector_widgets
        if selector_axis is None:
            return
        selector_axis.remove()
        selector_apply_axis.remove()
        selector_axis = selector_apply_axis = None
        selector_widgets = []
        strategy_axis.set_visible(True)
        matrix_axis.set_visible(True)
        change_button_axis.set_visible(True)
        date_input_axis.set_visible(True)
        update_control_layout()
        refresh_indicator_display()
        refresh_timeframe_visibility()
        draw_matrix()
        persist()
        fig.canvas.draw_idle()

    def on_control_click(event):
        if event.inaxes is change_button_axis:
            open_row_selector(event)
        elif event.inaxes is selector_apply_axis:
            apply_selection()

    def update_control_layout(_=None):
        nonlocal control_left
        figure_width, figure_height = fig.get_size_inches()
        minimum_height = minimum_figure_height()
        if figure_height < minimum_height:
            fig.set_size_inches(figure_width, minimum_height, forward=True)
            figure_height = minimum_height
        control_left = (figure_width - CONTROL_RIGHT_MARGIN_INCHES - CONTROL_WIDTH_INCHES) / figure_width
        strategy_axis.set_position(control_position(
            figure_height - STRATEGY_SECTION_TOP_INCHES - strategy_panel_height(),
            strategy_panel_height(),
        ))
        matrix_axis.set_position(control_position(
            matrix_panel_bottom(len(selection.visible_rows)), matrix_panel_height(len(selection.visible_rows))
        ))
        change_button_axis.set_position(control_position(
            button_bottom(len(selection.visible_rows)), BUTTON_HEIGHT_INCHES
        ))
        date_input_axis.set_position(control_position(
            DATE_INPUT_BOTTOM_INCHES, DATE_INPUT_HEIGHT_INCHES, DATE_INPUT_WIDTH_INCHES
        ))
        if selector_axis is not None:
            selector_axis.set_position(control_position(selector_panel_bottom(), selector_panel_height()))
            selector_apply_axis.set_position(control_position(BUTTON_GAP_INCHES, BUTTON_HEIGHT_INCHES))
            draw_selector_groups()
        else:
            # Rebuild with the new physical panel height so row and checkbox
            # sizes stay fixed while the number of displayed rows changes.
            draw_matrix()
        update_panels()
        update_month_grid()
        fig.canvas.draw_idle()

    refresh_strategy_controls()
    draw_matrix()
    refresh_lines()
    update_month_grid()
    refresh_timeframe_visibility()
    date_input.on_submit(on_start_date_submit)
    price_axis.callbacks.connect("xlim_changed", update_month_grid)
    price_axis.callbacks.connect("xlim_changed", refresh_timeframe_visibility)
    fig.canvas.mpl_connect("scroll_event", prevent_y_zoom_when_x_is_limited)
    fig.canvas.mpl_connect("button_press_event", on_zoom_press)
    fig.canvas.mpl_connect("button_press_event", on_rebalance_marker_click)
    fig.canvas.mpl_connect("button_press_event", on_chart_value_click)
    fig.canvas.mpl_connect("button_release_event", on_zoom_release)
    fig.canvas.mpl_connect("button_press_event", on_strategy_click)
    fig.canvas.mpl_connect("button_press_event", on_matrix_click)
    fig.canvas.mpl_connect("scroll_event", on_matrix_scroll)
    fig.canvas.mpl_connect("button_press_event", on_control_click)
    fig.canvas.mpl_connect("resize_event", update_control_layout)

    if SAVE_FIGURE:
        fig.savefig(RESULT_DIR / "strategy_comparison.png", dpi=300)
    if show_chart:
        plt.show()
    else:
        plt.close(fig)
