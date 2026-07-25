"""Interactive investment-strategy chart and controls."""

import json
from dataclasses import dataclass

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.widgets import CheckButtons, TextBox
import numpy as np
import pandas as pd

from config import DATA_DIR, FIGURE_SIZE, RESULT_DIR, SAVE_FIGURE, SHOW_CHART, TICKERS
APPLE_COLORS = ["#007AFF", "#FF9500", "#34C759", "#AF52DE", "#FF2D55", "#5AC8FA"]
LINE_STYLES = ["-", "--", ":", "-."]
SELECTION_FILE = RESULT_DIR / "chart_selection.json"
CONTROL_FONT_SIZE = 11
CONTROL_TITLE_X = 0.0
CONTROL_TITLE_Y = 1.0
CONTROL_TITLE_BOX_GAP_INCHES = 0.1
CHECKBOX_INCHES = 0.13
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
DATE_INPUT_HEIGHT_INCHES = 0.32
DATE_INPUT_WIDTH_INCHES = CONTROL_WIDTH_INCHES / 3
DATE_INPUT_LABEL_PAD = 0.08
SELECTOR_GROUP_GAP_INCHES = MATRIX_SECTION_GAP_INCHES
MATRIX_LINE_SAMPLE_WIDTH_INCHES = 0.42
MATRIX_LINE_TO_CHECKBOX_GAP_INCHES = 0.3
MATRIX_LABEL_X = 0.03
MATRIX_FIRST_COLUMN_X = 0.37
MATRIX_COLUMNS_WIDTH = 0.58
CHART_LEFT = 0.05
CHART_BOTTOM = 0.07
CHART_TOP = 0.93
CHART_TO_CONTROL_GAP = 0.04
PANEL_GAP = 0.025
PANEL_HEIGHT_WEIGHTS = {"price": 2.2, "oscillator": 1.4, "risk": 1.4}

INDICATORS = {
    "Price": ["Close"],
    "MA": ["MA20", "MA55", "MA120", "MA200"],
    "EMA": ["EMA20", "EMA55", "EMA120", "EMA200"],
    "RSI": ["RSI14"],
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
    **{name: "oscillator" for name in ("RSI", "MACD", "Stochastic")},
    **{name: "risk" for name in ("ROC", "TR", "ATR", "Volatility", "MDD")},
}
DETAIL_ROWS = {"MA", "EMA", "Bollinger", "MACD", "Stochastic", "ATR"}


@dataclass
class ChartSelection:
    strategies: dict
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
PANEL_ORDER = ("price", "oscillator", "risk")
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


def ticker_color(index, strategy_count):
    return APPLE_COLORS[(index + strategy_count) % len(APPLE_COLORS)]


def indicator_line_style(row):
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
    indicator, _ = MATRIX_ROWS[row]
    panel = PANEL_BY_INDICATOR[indicator]
    selected_indicators = selected_indicators_for_panel(panel, selection)
    if panel in {"oscillator", "risk"} and len(selected_indicators) == 1:
        return "-"
    return indicator_line_style(row)


def build_selection(results, market_data, saved):
    saved_strategies = saved.get("strategies", {}) if isinstance(saved.get("strategies"), dict) else {}
    saved_matrix = saved.get("matrix", {}) if isinstance(saved.get("matrix"), dict) else {}
    saved_tickers = saved.get("tickers", {}) if isinstance(saved.get("tickers"), dict) else {}
    saved_indicators = saved.get("indicators", {}) if isinstance(saved.get("indicators"), dict) else {}
    matrix_rows = list(MATRIX_ROWS)
    has_visible_rows = isinstance(saved.get("visible_rows"), list)
    has_visible_tickers = isinstance(saved.get("visible_tickers"), list)

    strategies = {
        result["strategy"].__class__.__name__: bool(saved_strategies.get(result["strategy"].__class__.__name__, True))
        for result in results
    }
    matrix = {
        row: {
            ticker: bool(
                saved_matrix.get(row, {}).get(
                    ticker,
                    saved_matrix.get(indicator, {}).get(
                        ticker,
                        bool(saved_tickers.get(ticker, True)) and bool(saved_indicators.get(indicator, indicator == "Price")),
                    ),
                )
            )
            for ticker in market_data
        }
        for row, (indicator, _) in MATRIX_ROWS.items()
    }
    visible_rows = [row for row in matrix_rows if row in saved["visible_rows"]] if has_visible_rows else matrix_rows
    visible_tickers = (
        [ticker for ticker in market_data if ticker in saved["visible_tickers"]]
        if has_visible_tickers
        else [ticker for ticker in market_data if saved_tickers.get(ticker, True)]
    )
    return ChartSelection(strategies, matrix, visible_rows, visible_tickers)


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


def load_market_data():
    market_data = {}
    for ticker in TICKERS:
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            print(f"Chart skipped for {ticker}: {path} not found")
            continue
        data = pd.read_csv(path, index_col="Date", parse_dates=True)
        if "Close" in data:
            market_data[ticker] = data
    return market_data


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


def rebalance_directions(history, trades, rebalances):
    """Return an up/down marker for each rebalance start date."""
    if trades is None or trades.empty or "Date" not in trades:
        return {}
    trades = trades.dropna(subset=["Date"]).copy()
    trades["Date"] = pd.to_datetime(trades["Date"])
    dates = (
        pd.to_datetime([event["Date"] for event in rebalances if event.get("Date") is not None])
        if rebalances
        else trades["Date"].drop_duplicates()
    )
    directions = {}
    for date in dates:
        if date not in history.index:
            continue
        day_trades = trades[trades["Date"] == date]
        weights = history.at[date, "Weights"]
        if day_trades.empty or not isinstance(weights, dict):
            continue
        net_shares = day_trades.groupby("Ticker")["Shares"].sum()
        candidates = [ticker for ticker in net_shares.index if ticker in weights]
        if not candidates:
            continue
        ticker = max(candidates, key=weights.get)
        if net_shares[ticker] != 0:
            directions[date] = "up" if net_shares[ticker] > 0 else "down"
    return directions


def draw_strategy_lines(price_axis, results, strategy_visibility):
    strategy_lines = {}
    strategy_markers = {}
    strategy_series = {}
    marker_dates = {}
    for index, result in enumerate(results):
        name = result["strategy"].__class__.__name__
        history = result["history"]
        strategy_series[name] = history["Portfolio"]
        values = index_to_start(strategy_series[name])
        (line,) = price_axis.plot(
            values.index,
            values,
            color=strategy_color(index),
            linewidth=1.2,
            solid_capstyle="round",
            visible=strategy_visibility[name],
        )
        strategy_lines[name] = line

        markers = []
        dates_by_marker = []
        for direction, marker in (("up", "^"), ("down", "v")):
            dates = [
                date
                for date, value in rebalance_directions(
                    history, result["trades"], result.get("rebalances", [])
                ).items()
                if value == direction
            ]
            points = values.reindex(dates).dropna()
            if not points.empty:
                markers.append(price_axis.scatter(
                    points.index, points.values, marker=marker, s=46,
                    color=line.get_color(), edgecolors="white", linewidths=0.7,
                    zorder=3, visible=strategy_visibility[name],
                ))
                dates_by_marker.append(dates)
        strategy_markers[name] = markers
        marker_dates[name] = dates_by_marker
    return strategy_lines, strategy_markers, strategy_series, marker_dates


def draw_indicator_lines(panel_axes, market_data, chart_start, strategy_count, selection):
    panel_lines = {"price": [], "oscillator": [], "risk": []}
    indicator_lines = {}
    indicator_series = {}
    for ticker_index, (ticker, data) in enumerate(market_data.items()):
        if chart_start is not None:
            data = data.loc[data.index >= chart_start]
        color = ticker_color(ticker_index, strategy_count)
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



def draw_chart(results, price_data=None, show_chart=SHOW_CHART):
    """Draw strategy performance and a ticker-by-indicator selection matrix."""
    market_data = price_data if price_data is not None else load_market_data()
    selection = build_selection(results, market_data, load_selection())
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
    if fig.canvas.manager is not None:
        fig.canvas.manager.set_window_title("투자 전략")
    style_axes(fig, axes)

    default_start_date = min(
        (result["history"].index[0] for result in results if not result["history"].empty),
        default=None,
    )
    active_start_date = default_start_date
    strategy_lines, strategy_markers, strategy_series, marker_dates = draw_strategy_lines(
        price_axis, results, selection.strategies
    )
    panel_lines, indicator_lines, indicator_series = draw_indicator_lines(
        panel_axes, market_data, active_start_date, len(results), selection
    )
    panel_lines["price"].extend(strategy_lines.values())

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
            return
        available = top - bottom - PANEL_GAP * (len(visible) - 1)
        current_top = top
        for panel, axis in panel_axes.items():
            if panel not in visible:
                axis.set_visible(False)
                continue
            height = available * PANEL_HEIGHT_WEIGHTS[panel] / sum(PANEL_HEIGHT_WEIGHTS[item] for item in visible)
            axis.set_visible(True)
            axis.set_position([left, current_top - height, right - left, height])
            current_top -= height + PANEL_GAP
        for axis in axes:
            axis.tick_params(labelbottom=axis in (panel_axes[panel] for panel in visible))

    def persist():
        selection.strategies = {name: line.get_visible() for name, line in strategy_lines.items()}
        save_selection(selection)

    def refresh_lines():
        for name, line in strategy_lines.items():
            values = series_from_start(strategy_series[name], active_start_date, normalize=True)
            line.set_data(values.index, values)
            for marker, dates in zip(strategy_markers[name], marker_dates[name]):
                points = values.reindex(dates).dropna()
                marker.set_offsets(np.column_stack((marker.axes.convert_xunits(points.index), points.values)))
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
        refresh_lines()
        if active_start_date is not None:
            price_axis.set_xlim(left=active_start_date)
        fig.canvas.draw_idle()

    def reset_start_date_on_home(axis):
        nonlocal active_start_date
        if active_start_date is None or default_start_date is None:
            return
        if active_start_date == default_start_date:
            return
        view_start, _ = axis.get_xlim()
        if view_start > mdates.date2num(default_start_date):
            return
        active_start_date = default_start_date
        date_input.eventson = False
        date_input.set_val(initial_date_text)
        date_input.eventson = True
        refresh_lines()
        price_axis.relim()
        price_axis.autoscale_view(scalex=True, scaley=False)

    def add_checkbox(axis, x, y):
        figure_width, figure_height = fig.get_size_inches()
        bounds = axis.get_position()
        box_width = CHECKBOX_INCHES / (bounds.width * figure_width)
        box_height = CHECKBOX_INCHES / (bounds.height * figure_height)
        box = Rectangle((x - box_width / 2, y - box_height / 2), box_width, box_height, transform=axis.transAxes,
                        facecolor="#FFFFFF", edgecolor="#1D1D1F", linewidth=1.0)
        axis.add_patch(box)
        mark = axis.text(x, y, "×", transform=axis.transAxes, ha="center", va="center",
                         fontsize=CONTROL_FONT_SIZE, color="#1D1D1F")
        return box, mark

    def control_position(bottom, height, width=CONTROL_WIDTH_INCHES):
        figure_width, figure_height = fig.get_size_inches()
        left = (figure_width - CONTROL_RIGHT_MARGIN_INCHES - width) / figure_width
        return [left, bottom / figure_height, width / figure_width, height / figure_height]

    def strategy_panel_height():
        return 2 * CONTROL_BOX_PADDING_INCHES + max(len(strategy_names) - 1, 0) * CONTROL_ROW_SPACING_INCHES

    def matrix_panel_height(rows):
        return (
            2 * CONTROL_BOX_PADDING_INCHES
            + MATRIX_TICKER_HEADER_HEIGHT_INCHES
            + max(rows - 1, 0) * CONTROL_ROW_SPACING_INCHES
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
    for index, name in enumerate(strategy_names):
        y = 1 - (CONTROL_BOX_PADDING_INCHES + index * CONTROL_ROW_SPACING_INCHES) / strategy_panel_height()
        box, mark = add_checkbox(strategy_axis, 0.08, y)
        strategy_axis.text(0.15, y, name, transform=strategy_axis.transAxes, va="center", fontsize=CONTROL_FONT_SIZE,
                           color=strategy_lines[name].get_color())
        strategy_controls.append((name, box, mark))

    def refresh_strategy_controls():
        for name, _, mark in strategy_controls:
            mark.set_visible(strategy_lines[name].get_visible())

    def on_strategy_click(event):
        if event.inaxes is not strategy_axis:
            return
        for name, box, _ in strategy_controls:
            if box.contains(event)[0]:
                line = strategy_lines[name]
                line.set_visible(not line.get_visible())
                for marker in strategy_markers[name]:
                    marker.set_visible(line.get_visible())
                refresh_strategy_controls()
                update_panels()
                rescale()
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
    # Matplotlib's TextBox registers a resize callback wrapped as a mouse event
    # handler, which raises AttributeError on ResizeEvent in the installed version.
    date_input_resize_cid = date_input._cids.pop()
    fig.canvas.mpl_disconnect(date_input_resize_cid)
    selector_axis = None
    selector_apply_axis = None
    selector_widgets = []

    def draw_matrix():
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
                             fontsize=CONTROL_FONT_SIZE, color=ticker_color(index, len(results)))
        for row_index, row in enumerate(selection.visible_rows):
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
                refresh_lines()
                persist()
                fig.canvas.draw_idle()
                return

    def toggle_selector_selection(group, item):
        if group == "tickers":
            if item in selection.visible_tickers:
                selection.visible_tickers.remove(item)
            else:
                selection.visible_tickers[:] = [
                    ticker for ticker in market_data if ticker in {*selection.visible_tickers, item}
                ]
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
                    widget = CheckButtons(checkbox_axis, [row], [selected])
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
        refresh_lines()
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
        update_panels()
        fig.canvas.draw_idle()

    refresh_strategy_controls()
    draw_matrix()
    refresh_lines()
    date_input.on_submit(on_start_date_submit)
    price_axis.callbacks.connect("xlim_changed", reset_start_date_on_home)
    fig.canvas.mpl_connect("button_press_event", on_strategy_click)
    fig.canvas.mpl_connect("button_press_event", on_matrix_click)
    fig.canvas.mpl_connect("button_press_event", on_control_click)
    fig.canvas.mpl_connect("resize_event", update_control_layout)

    if SAVE_FIGURE:
        fig.savefig(RESULT_DIR / "strategy_comparison.png", dpi=300)
    if show_chart:
        plt.show()
    else:
        plt.close(fig)
