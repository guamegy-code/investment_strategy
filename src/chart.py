"""Interactive investment-strategy chart and controls."""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.widgets import CheckButtons
import pandas as pd

from config import DATA_DIR, FIGURE_SIZE, RESULT_DIR, SAVE_FIGURE, SHOW_CHART, TICKERS
APPLE_COLORS = ["#007AFF", "#FF9500", "#34C759", "#AF52DE", "#FF2D55", "#5AC8FA"]
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
SELECTOR_GROUP_GAP_INCHES = MATRIX_SECTION_GAP_INCHES
MATRIX_LINE_SAMPLE_WIDTH_INCHES = 0.42
MATRIX_LINE_TO_CHECKBOX_GAP_INCHES = 0.3

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


def save_selection(strategies, matrix, visible_rows, visible_tickers):
    with SELECTION_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "strategies": strategies,
                "matrix": matrix,
                "visible_rows": visible_rows,
                "visible_tickers": visible_tickers,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )


def style_axes(fig, axes):
    fig.patch.set_facecolor("#F5F5F7")
    for axis in axes:
        axis.set_facecolor("#FFFFFF")
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#E5E5EA", linewidth=0.8)
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
    return series / valid.iloc[0]


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



def draw_chart(results, price_data=None, show_chart=SHOW_CHART):
    """Draw strategy performance and a ticker-by-indicator selection matrix."""
    market_data = price_data if price_data is not None else load_market_data()
    saved = load_selection()
    saved_strategies = saved.get("strategies", {}) if isinstance(saved.get("strategies"), dict) else {}
    saved_matrix = saved.get("matrix", {}) if isinstance(saved.get("matrix"), dict) else {}
    saved_tickers = saved.get("tickers", {}) if isinstance(saved.get("tickers"), dict) else {}
    saved_indicators = saved.get("indicators", {}) if isinstance(saved.get("indicators"), dict) else {}
    saved_visible_rows = saved.get("visible_rows", [])
    saved_visible_rows = saved_visible_rows if isinstance(saved_visible_rows, list) else []
    saved_visible_tickers = saved.get("visible_tickers", [])
    saved_visible_tickers = saved_visible_tickers if isinstance(saved_visible_tickers, list) else []

    strategy_state = {
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

    strategy_names = list(strategy_state)
    matrix_rows = list(MATRIX_ROWS)
    visible_rows = [row for row in matrix_rows if row in saved_visible_rows]
    if not saved_visible_rows:
        visible_rows = matrix_rows.copy()
    visible_tickers = [ticker for ticker in market_data if ticker in saved_visible_tickers]
    if not saved_visible_tickers:
        visible_tickers = [ticker for ticker in market_data if saved_tickers.get(ticker, True)]
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

    chart_start = min((result["history"].index[0] for result in results), default=None)
    strategy_lines = {}
    strategy_markers = {}
    panel_lines = {"price": [], "oscillator": [], "risk": []}
    indicator_lines = {}

    for index, result in enumerate(results):
        name = result["strategy"].__class__.__name__
        history = result["history"]
        values = index_to_start(history["Portfolio"])
        (line,) = price_axis.plot(
            values.index,
            values,
            color=APPLE_COLORS[index % len(APPLE_COLORS)],
            linewidth=1.2,
            solid_capstyle="round",
            visible=strategy_state[name],
        )
        strategy_lines[name] = line
        panel_lines["price"].append(line)

        markers = []
        for direction, marker in (("up", "^"), ("down", "v")):
            dates = [date for date, value in rebalance_directions(history, result["trades"], result.get("rebalances", [] )).items() if value == direction]
            points = values.reindex(dates).dropna()
            if not points.empty:
                markers.append(price_axis.scatter(
                    points.index, points.values, marker=marker, s=46,
                    color=line.get_color(), edgecolors="white", linewidths=0.7,
                    zorder=3, visible=strategy_state[name],
                ))
        strategy_markers[name] = markers

    line_styles = ["-", "--", ":", "-."]
    for ticker_index, (ticker, data) in enumerate(market_data.items()):
        if chart_start is not None:
            data = data.loc[data.index >= chart_start]
        color = APPLE_COLORS[(ticker_index + len(results)) % len(APPLE_COLORS)]
        for style_index, (indicator, columns) in enumerate(INDICATORS.items()):
            axis = panel_axes[PANEL_BY_INDICATOR[indicator]]
            for column in columns:
                if column not in data or data[column].dropna().empty:
                    continue
                values = data[column]
                if indicator in INDEXED_INDICATORS:
                    values = index_to_start(values)
                row = ROW_FOR_COLUMN[(indicator, column)]
                (line,) = axis.plot(
                    values.index, values, color=color,
                    linestyle=line_styles[style_index % len(line_styles)],
                    linewidth=0.8, alpha=0.8, solid_capstyle="round",
                    visible=matrix[row][ticker],
                )
                indicator_lines[(row, ticker, column)] = line
                panel_lines[PANEL_BY_INDICATOR[indicator]].append(line)

    def rescale():
        for panel, axis in panel_axes.items():
            if any(line.get_visible() for line in panel_lines[panel]):
                axis.relim(visible_only=True)
                axis.autoscale_view(scalex=False, scaley=True)

    def update_panels():
        visible = [panel for panel, lines in panel_lines.items() if any(line.get_visible() for line in lines)]
        left, right, bottom, top = 0.05, control_left - 0.04, 0.07, 0.93
        if not visible:
            for axis in axes:
                axis.set_visible(False)
            return
        weights = {"price": 2.2, "oscillator": 1.4, "risk": 1.4}
        gap = 0.025
        available = top - bottom - gap * (len(visible) - 1)
        current_top = top
        for panel, axis in panel_axes.items():
            if panel not in visible:
                axis.set_visible(False)
                continue
            height = available * weights[panel] / sum(weights[item] for item in visible)
            axis.set_visible(True)
            axis.set_position([left, current_top - height, right - left, height])
            current_top -= height + gap
        last_axis = panel_axes[visible[-1]]
        for axis in axes:
            axis.tick_params(labelbottom=axis is last_axis)

    def persist():
        save_selection(
            {name: line.get_visible() for name, line in strategy_lines.items()},
            matrix,
            visible_rows,
            visible_tickers,
        )

    def refresh_lines():
        for (row, ticker, _), line in indicator_lines.items():
            line.set_visible(row in visible_rows and ticker in visible_tickers and matrix[row][ticker])
        update_panels()
        rescale()

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

    def control_position(bottom, height):
        figure_width, figure_height = fig.get_size_inches()
        left = (figure_width - CONTROL_RIGHT_MARGIN_INCHES - CONTROL_WIDTH_INCHES) / figure_width
        return [left, bottom / figure_height, CONTROL_WIDTH_INCHES / figure_width, height / figure_height]

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
    matrix_axis = fig.add_axes(control_position(matrix_panel_bottom(len(visible_rows)), matrix_panel_height(len(visible_rows))))
    column_start = 0.37
    matrix_controls = []
    change_button_axis = fig.add_axes(control_position(button_bottom(len(visible_rows)), BUTTON_HEIGHT_INCHES))
    style_control_axis(change_button_axis, "")
    change_button_axis.text(0.5, 0.5, "항목 변경", transform=change_button_axis.transAxes,
                            ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#1D1D1F")
    selector_axis = None
    selector_apply_axis = None
    selector_widgets = []

    def indicator_line_style(row):
        indicator, _ = MATRIX_ROWS[row]
        style_index = list(INDICATORS).index(indicator)
        return line_styles[style_index % len(line_styles)]

    def draw_matrix():
        matrix_axis.clear()
        style_control_axis(matrix_axis, "지표 × 종목")
        matrix_controls.clear()
        if not visible_rows:
            matrix_axis.text(0.5, 0.5, "표시할 지표 항목이 없습니다.", transform=matrix_axis.transAxes,
                             ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#6E6E73")
            return
        matrix_tickers = [ticker for ticker in market_data if ticker in visible_tickers]
        if not matrix_tickers:
            matrix_axis.text(0.5, 0.5, "표시할 종목이 없습니다.", transform=matrix_axis.transAxes,
                             ha="center", va="center", fontsize=CONTROL_FONT_SIZE, color="#6E6E73")
            return
        column_step = 0.58 / len(matrix_tickers)
        height = matrix_panel_height(len(visible_rows))
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
        line_end = column_start - checkbox_width / 2 - line_gap
        line_start = line_end - line_width
        for index, ticker in enumerate(matrix_tickers):
            x = column_start + index * column_step
            matrix_axis.text(x, ticker_title_y, ticker, transform=matrix_axis.transAxes, ha="center", va="center",
                             fontsize=CONTROL_FONT_SIZE, color=APPLE_COLORS[(index + len(results)) % len(APPLE_COLORS)])
        for row_index, row in enumerate(visible_rows):
            y = row_start - row_index * row_step
            matrix_axis.text(0.03, y, row, transform=matrix_axis.transAxes, va="center",
                             fontsize=CONTROL_FONT_SIZE, color="#333333")
            matrix_axis.plot(
                [line_start, line_end],
                [y, y],
                transform=matrix_axis.transAxes,
                color="#333333",
                linestyle=indicator_line_style(row),
                linewidth=1.4,
                solid_capstyle="round",
            )
            for ticker_index, ticker in enumerate(matrix_tickers):
                x = column_start + ticker_index * column_step
                box, mark = add_checkbox(matrix_axis, x, y)
                matrix_controls.append((row, ticker, box, mark))
        refresh_matrix_controls()

    def refresh_matrix_controls():
        for row, ticker, _, mark in matrix_controls:
            mark.set_visible(matrix[row][ticker])

    def on_matrix_click(event):
        if event.inaxes is not matrix_axis:
            return
        for row, ticker, box, _ in matrix_controls:
            if box.contains(event)[0]:
                matrix[row][ticker] = not matrix[row][ticker]
                refresh_matrix_controls()
                refresh_lines()
                persist()
                fig.canvas.draw_idle()
                return

    def toggle_selector_selection(group, item):
        if group == "tickers":
            if item in visible_tickers:
                visible_tickers.remove(item)
            else:
                visible_tickers[:] = [ticker for ticker in market_data if ticker in {*visible_tickers, item}]
            return
        if item in visible_rows:
            visible_rows.remove(item)
        else:
            visible_rows[:] = [row for row in matrix_rows if row in {*visible_rows, item}]

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
                    selected = row in visible_tickers if panel == "tickers" else row in visible_rows
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
        matrix_axis.set_position(control_position(matrix_panel_bottom(len(visible_rows)), matrix_panel_height(len(visible_rows))))
        change_button_axis.set_position(control_position(button_bottom(len(visible_rows)), BUTTON_HEIGHT_INCHES))
        if selector_axis is not None:
            selector_axis.set_position(control_position(selector_panel_bottom(), selector_panel_height()))
            selector_apply_axis.set_position(control_position(BUTTON_GAP_INCHES, BUTTON_HEIGHT_INCHES))
            draw_selector_groups()
        update_panels()
        fig.canvas.draw_idle()

    refresh_strategy_controls()
    draw_matrix()
    refresh_lines()
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
