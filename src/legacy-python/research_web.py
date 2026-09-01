"""Dash/Plotly research views for completed backtest results.

This module deliberately consumes the result dictionaries produced by ``Runner``.
It does not evaluate strategies or place orders, so the research UI remains separate
from the strategy and rebalancing domain interfaces.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from threading import RLock
from typing import Any, Iterable
import unicodedata

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dcc, html
from dash.exceptions import PreventUpdate
import dash_ag_grid as dag
from flask import request

from indicator_catalog import (
    INDICATOR_LABELS,
    PANEL_LABELS,
    PANEL_ORDER,
    indicator_is_indexed,
    indicator_options,
    indicator_panel,
)
from strategy_domain import strategy_display_name
from strategy_runtime import StrategyResultSnapshot


# TradingView Markets에서 참고한 밝은 금융 정보 화면의 대비·간격 원칙입니다.
# 로고·자산·코드를 복제하지 않고, 이 프로젝트의 연구 화면에 맞춘 색상 토큰입니다.
TV_COLORS = ("#2962FF", "#089981", "#FF9800", "#9C27B0", "#F23645", "#00BCD4")
TV_TEXT = "#131722"
TV_MUTED = "#6A6D78"
TV_GRID = "#E0E3EB"
TV_BORDER = "#E0E3EB"
TV_POSITIVE = "#089981"
TV_NEGATIVE = "#F23645"
UI_FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, '
    '"Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif'
)
TABLER_STYLESHEET = "https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css"
TABLER_ICONS_STYLESHEET = "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.40.0/dist/tabler-icons.min.css"
PRODUCT_DISPLAY_NAMES = {
    "379810.KS": "KODEX 미국나스닥100",
    "426030.KS": "TIME 미국나스닥100액티브",
    "0015B0.KS": "KoAct 미국나스닥성장액티브",
    "069500.KS": "KODEX 200 (069500.KS)",
    "114100.KS": "KODEX 국고채 3년 (114100.KS)",
    "148070.KS": "KOSEF 국고채 10년 (148070.KS)",
}
HOVER_FIGURE_SPACE = "\u2007"


def _display_width(value: str) -> int:
    """Return the terminal-style width used to align mixed Korean/Latin labels."""
    return sum(
        2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        for character in value
    )


def _padded_hover_labels(labels: Iterable[str]) -> dict[str, str]:
    labels = list(labels)
    width = max((_display_width(label) for label in labels), default=0)
    return {
        label: label + HOVER_FIGURE_SPACE * (width - _display_width(label))
        for label in labels
    }


def _hover_values(values: Iterable[Any], suffix: str = "%", width: int = 10) -> list[str]:
    """Pre-format hover numbers so signs and decimal points share one column."""
    return [
        f"{float(value):,.2f}{suffix}".rjust(width, HOVER_FIGURE_SPACE)
        for value in values
    ]


def _visible_rebalance_y_range(
    figure: dict[str, Any] | None, relayout_data: dict[str, Any] | None,
) -> list[float] | None:
    """Return a padded y range for line values inside the visible x window."""
    if not figure or not relayout_data:
        return None
    explicit_range = relayout_data.get("xaxis.range")
    if explicit_range and len(explicit_range) == 2:
        start, end = explicit_range
    elif "xaxis.range[0]" in relayout_data and "xaxis.range[1]" in relayout_data:
        start = relayout_data["xaxis.range[0]"]
        end = relayout_data["xaxis.range[1]"]
    elif relayout_data.get("xaxis.autorange"):
        start = end = None
    else:
        return None

    start_at = pd.Timestamp(start) if start is not None else None
    end_at = pd.Timestamp(end) if end is not None else None
    visible_values: list[float] = []
    for trace in figure.get("data", []):
        if "lines" not in str(trace.get("mode", "")):
            continue
        if trace.get("yaxis", "y") != "y":
            continue
        y_values = trace.get("y", [])
        if isinstance(y_values, dict) and {"dtype", "bdata"} <= y_values.keys():
            y_values = np.frombuffer(
                base64.b64decode(y_values["bdata"]), dtype=np.dtype(y_values["dtype"])
            )
        for x_value, y_value in zip(trace.get("x", []), y_values):
            point_at = pd.Timestamp(x_value)
            if start_at is not None and point_at < start_at:
                continue
            if end_at is not None and point_at > end_at:
                continue
            if y_value is not None and not pd.isna(y_value):
                visible_values.append(float(y_value))
    if not visible_values:
        return None

    minimum = min(visible_values)
    maximum = max(visible_values)
    if any(trace.get("stackgroup") for trace in figure.get("data", [])):
        lower = min(minimum, 0.0)
        upper = max(maximum, 0.0)
        padding = max((upper - lower) * 0.06, 0.1)
        return [lower - padding if lower < 0 else 0.0,
                upper + padding if upper > 0 else 0.0]
    span = maximum - minimum
    padding = max(span * 0.08, max(abs(minimum), abs(maximum), 1.0) * 0.02, 0.1)
    return [minimum - padding, maximum + padding]


def _visible_indicator_y_ranges(
    figure: dict[str, Any] | None, visible_range: Iterable[Any] | None,
) -> dict[str, list[float]]:
    """Return a padded Y range per indicator panel for the visible dates."""
    if not figure or not visible_range:
        return {}
    bounds = list(visible_range)
    if len(bounds) != 2:
        return {}
    start_at, end_at = (pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1]))
    values_by_axis: dict[str, list[float]] = {}
    for trace in figure.get("data", []):
        if "lines" not in str(trace.get("mode", "")):
            continue
        y_values = trace.get("y", [])
        if isinstance(y_values, dict) and {"dtype", "bdata"} <= y_values.keys():
            y_values = np.frombuffer(
                base64.b64decode(y_values["bdata"]), dtype=np.dtype(y_values["dtype"])
            )
        axis_reference = trace.get("yaxis", "y")
        axis_name = "yaxis" if axis_reference == "y" else f"yaxis{axis_reference[1:]}"
        for x_value, y_value in zip(trace.get("x", []), y_values):
            point_at = pd.Timestamp(x_value)
            if point_at < start_at or point_at > end_at:
                continue
            if y_value is not None and not pd.isna(y_value):
                values_by_axis.setdefault(axis_name, []).append(float(y_value))
    ranges: dict[str, list[float]] = {}
    for axis_name, values in values_by_axis.items():
        minimum, maximum = min(values), max(values)
        span = maximum - minimum
        padding = max(
            span * 0.08,
            max(abs(minimum), abs(maximum), 1.0) * 0.02,
            0.1,
        )
        ranges[axis_name] = [minimum - padding, maximum + padding]
    return ranges


def _detail_range_state(
    relayout_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract a reusable x-axis range from a Plotly relayout event."""
    if not relayout_data:
        return None
    axis_names = sorted(
        {
            key.split(".", 1)[0]
            for key in relayout_data
            if key.startswith("xaxis")
        } | {"xaxis"},
        key=lambda name: int(name[5:] or "1"),
        reverse=True,
    )
    for axis_name in axis_names:
        explicit_range = relayout_data.get(f"{axis_name}.range")
        if explicit_range and len(explicit_range) == 2:
            return {"range": [explicit_range[0], explicit_range[1]]}
        if (
            f"{axis_name}.range[0]" in relayout_data
            and f"{axis_name}.range[1]" in relayout_data
        ):
            return {"range": [
                relayout_data[f"{axis_name}.range[0]"],
                relayout_data[f"{axis_name}.range[1]"],
            ]}
        if relayout_data.get(f"{axis_name}.autorange"):
            return {"autorange": True}
    return None


def _apply_stored_detail_range(
    figure: go.Figure, stored_range: dict[str, Any] | None,
) -> go.Figure:
    """Apply a shared detail range without shortening it at data boundaries."""
    bounded_range = _bounded_detail_range(figure, stored_range)
    requested = (bounded_range or {}).get("range")
    if not requested:
        figure.update_xaxes(autorange=True)
        return figure
    figure.update_xaxes(range=requested, autorange=False)
    return figure


def _bounded_detail_range(
    figure: go.Figure | dict[str, Any], stored_range: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Clamp a date window by shifting it, preserving its requested duration."""
    if not stored_range:
        return None
    requested = stored_range.get("range")
    if not requested or len(requested) != 2:
        return {"autorange": True} if stored_range.get("autorange") else None
    layout = (
        figure.to_plotly_json().get("layout", {})
        if isinstance(figure, go.Figure)
        else figure.get("layout", {})
    )
    axis_names = sorted(
        (name for name in layout if name.startswith("xaxis")),
        key=lambda name: int(name[5:] or "1"),
        reverse=True,
    )
    xaxis = next(
        (
            layout[name] for name in axis_names
            if layout.get(name, {}).get("rangeslider", {}).get("range")
        ),
        layout.get("xaxis", {}),
    )
    minimum = xaxis.get("minallowed")
    maximum = xaxis.get("maxallowed")
    slider_range = xaxis.get("rangeslider", {}).get("range")
    if (minimum is None or maximum is None) and slider_range:
        minimum, maximum = slider_range
    if minimum is None or maximum is None:
        return {"autorange": True}

    lower_bound = pd.Timestamp(minimum)
    upper_bound = pd.Timestamp(maximum)
    start_at = pd.Timestamp(requested[0])
    end_at = pd.Timestamp(requested[1])
    duration = end_at - start_at
    available = upper_bound - lower_bound
    if duration <= pd.Timedelta(0) or duration >= available:
        start_at, end_at = lower_bound, upper_bound
    elif start_at < lower_bound:
        start_at, end_at = lower_bound, lower_bound + duration
    elif end_at > upper_bound:
        start_at, end_at = upper_bound - duration, upper_bound
    return {"range": [start_at.isoformat(), end_at.isoformat()]}


def _apply_chart_style(figure: go.Figure, **layout) -> go.Figure:
    """Apply a compact chart theme that fits inside a Tabler card."""
    figure.update_layout(
        colorway=TV_COLORS,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": UI_FONT_FAMILY, "color": TV_TEXT, "size": 13},
        margin={"l": 56, "r": 20, "t": 48, "b": 54, "autoexpand": True},
        legend={
            "orientation": "h", "y": 1.02, "x": 0, "yanchor": "bottom",
            # Plotly reverses legends for stacked traces unless this is explicit.
            # Keep the legend in the strategy's asset declaration order.
            "traceorder": "normal", "font": {"size": 12}, "title": None,
        },
        hoverlabel={
            "bgcolor": "rgba(255,255,255,0.96)", "font": {
                "color": "#182433", "size": 12, "weight": "normal",
                "family": UI_FONT_FAMILY,
            },
            "grouptitlefont": {
                "color": "#667382", "size": 11, "weight": "normal",
                "family": UI_FONT_FAMILY,
            },
            "bordercolor": "#DCE1E7", "align": "left", "namelength": -1,
        },
        hovermode="x unified",
        hoverdistance=-1,
        spikedistance=-1,
        autosize=True,
        **layout,
    )
    figure.update_xaxes(
        showgrid=True, gridcolor=TV_GRID, linecolor=TV_BORDER, zeroline=False,
        fixedrange=False, automargin=True, tickfont={"size": 12}, nticks=8,
        hoverformat="%Y.%m.%d",
    )
    figure.update_yaxes(
        showgrid=True, gridcolor=TV_GRID, linecolor=TV_BORDER, zeroline=False,
        fixedrange=False, automargin=True, tickfont={"size": 12},
        title_font={"size": 13}, title_standoff=10,
    )
    figure.update_traces(line={"width": 1.8}, selector={"type": "scatter"})
    return figure


def _apply_detail_navigation(
    figure: go.Figure, *, show_range_controls: bool,
    show_range_slider: bool | None = None,
    show_range_selector: bool | None = None,
    slider_thickness: float = 0.09,
    separate_selector_row: bool = False,
    legend_columns: int = 2,
    legend_plot_gap: int = 6,
    selector_legend_gap: int = 14,
    slider_height_px: float | None = None,
    slider_label_offset_px: float | None = None,
) -> go.Figure:
    """Use finance-style horizontal navigation for detail time-series charts."""
    slider_visible = (
        show_range_controls if show_range_slider is None else show_range_slider
    )
    selector_visible = (
        show_range_controls if show_range_selector is None else show_range_selector
    )
    legend_columns = max(1, legend_columns)
    legend_rows = max(1, (sum(
        trace.showlegend is not False and bool(trace.name)
        for trace in figure.data
    ) + legend_columns - 1) // legend_columns)
    top_margin = (
        max(96, 32 + selector_legend_gap + 19 * legend_rows + legend_plot_gap)
        if selector_visible and separate_selector_row
        else 96 if selector_visible else 48
    )
    bottom_margin = 78 if slider_visible else 54
    rendered_height = int(figure.layout.height or 450)
    # Convert pixel gaps into Plotly paper coordinates so they remain constant
    # as the number of legend rows and the chart height change.
    plot_height = max(rendered_height - top_margin - bottom_margin, 1)
    if slider_visible and slider_height_px is not None:
        slider_thickness = slider_height_px / plot_height
    slider_label_y = (
        -slider_label_offset_px / plot_height
        if slider_label_offset_px is not None else -0.235
    )
    legend_y = 1 + legend_plot_gap / plot_height
    selector_y = (
        legend_y + (19 * legend_rows + selector_legend_gap) / plot_height
    )
    dates = sorted({
        pd.Timestamp(value)
        for trace in figure.data
        for value in (trace.x if trace.x is not None else [])
        if value is not None
    })
    start_at = dates[0] if dates else None
    end_at = dates[-1] if dates else None
    figure.update_layout(
        dragmode="pan",
        margin={
            "l": 56, "r": 20,
            "t": top_margin,
            "b": bottom_margin,
            "autoexpand": True,
        },
    )
    if separate_selector_row:
        figure.update_layout(legend={
            "y": legend_y,
            "entrywidth": 1 / legend_columns,
            "entrywidthmode": "fraction",
        })
    xaxis_options: dict[str, Any] = {
        "fixedrange": False,
        "type": "date",
        "tickformat": "%Y.%m",
        "tickformatstops": [
            {"dtickrange": [None, "M1"], "value": "%Y.%m.%d"},
            {"dtickrange": ["M1", None], "value": "%Y.%m"},
        ],
        "rangeslider": {
            "visible": slider_visible,
            "thickness": slider_thickness,
            "bgcolor": (
                "rgba(32,107,196,0.09)"
                if slider_thickness >= 0.12
                else "rgba(106,109,120,0.06)"
            ),
            "bordercolor": (
                "rgba(32,107,196,0.42)"
                if slider_thickness >= 0.12 else TV_BORDER
            ),
            "borderwidth": 1,
        },
    }
    if selector_visible:
        xaxis_options["rangeselector"] = {
            "buttons": [
                {"count": 1, "label": "1년", "step": "year", "stepmode": "backward"},
                {"count": 3, "label": "3년", "step": "year", "stepmode": "backward"},
                {"count": 5, "label": "5년", "step": "year", "stepmode": "backward"},
                {"label": "전체", "step": "all"},
            ],
            "x": 0 if separate_selector_row else 1,
            "xanchor": "left" if separate_selector_row else "right",
            "y": selector_y if separate_selector_row else 1.18,
            # Keep the selector on its own row above the horizontal legend.
            "yanchor": "bottom",
            "bgcolor": "rgba(106,109,120,0.08)",
            "activecolor": "rgba(41,98,255,0.18)",
            "bordercolor": TV_BORDER,
            "borderwidth": 1,
            "font": {"size": 11, "weight": "normal"},
        }
    if start_at is not None and end_at is not None:
        start_value = start_at.isoformat()
        end_value = end_at.isoformat()
        xaxis_options["rangeslider"]["range"] = [start_value, end_value]
        if start_at < end_at:
            # Enforce the same hard bounds for plot panning and the range slider.
            # The relayout callbacks below additionally preserve the window width.
            xaxis_options["minallowed"] = start_value
            xaxis_options["maxallowed"] = end_value

        if start_at.year == end_at.year:
            year_marks = [(0.5, str(start_at.year))]
        else:
            year_span = end_at.year - start_at.year
            raw_step = max(1, (year_span + 9) // 10)
            year_step = next(
                step for step in (1, 2, 3, 5, 10, 20, 50, 100)
                if step >= raw_step
            )
            mark_dates = [start_at]
            mark_dates.extend(
                pd.Timestamp(year=year, month=1, day=1)
                for year in range(start_at.year + 1, end_at.year + 1)
                if year % year_step == 0
            )
            mark_dates.append(end_at)
            duration = max((end_at - start_at).total_seconds(), 1.0)
            year_marks = []
            seen_years = set()
            for mark_at in mark_dates:
                if mark_at.year in seen_years:
                    continue
                seen_years.add(mark_at.year)
                position = (mark_at - start_at).total_seconds() / duration
                year_marks.append((min(max(position, 0.0), 1.0), str(mark_at.year)))
        if slider_visible:
            for position, label in year_marks:
                figure.add_annotation(**{
                    "x": position,
                    "xref": "paper",
                    "y": slider_label_y,
                    "yref": "paper",
                    "text": label,
                    "showarrow": False,
                    "font": {"size": 10, "color": TV_MUTED},
                    "xanchor": "center",
                    "yanchor": "top",
                })
    axis_numbers = [
        1 if getattr(trace, "xaxis", None) in {None, "x"}
        else int(str(trace.xaxis)[1:])
        for trace in figure.data
        if getattr(trace, "xaxis", None) is not None
    ]
    bottom_axis_number = max(axis_numbers, default=1)
    uses_shared_subplots = bottom_axis_number > 1
    if uses_shared_subplots:
        common_options = {
            key: value for key, value in xaxis_options.items()
            if key not in {"rangeslider", "rangeselector"}
        }
        figure.update_xaxes(**common_options)
        bottom_axis = getattr(figure.layout, f"xaxis{bottom_axis_number}")
        bottom_axis.update(**xaxis_options, showticklabels=True)
        for axis_number in range(1, bottom_axis_number):
            axis_name = "xaxis" if axis_number == 1 else f"xaxis{axis_number}"
            getattr(figure.layout, axis_name).update(showticklabels=False)
    else:
        figure.update_xaxes(**xaxis_options)
    figure.update_yaxes(fixedrange=True)
    return figure


def _metric_value(value: Any, kind: str) -> str:
    """Format a dashboard headline metric without leaking pandas/numpy types."""
    if value is None or pd.isna(value):
        return "—"
    if kind == "percent":
        return f"{float(value):.2%}"
    if kind == "amount":
        return f"{float(value):,.0f}"
    return f"{float(value):.2f}"


def _icon(name: str) -> html.I:
    return html.I(className=f"ti ti-{name}", **{"aria-hidden": "true"})


def _chart_card(title: str, description: str, graph: Any, icon: str) -> html.Section:
    return html.Section([
        html.Div([
            html.Div([html.Span(_icon(icon), className="research-card-icon"), html.Div([
                html.H2(title, className="card-title"),
                html.P(description, className="research-card-description"),
            ])], className="research-card-heading"),
        ], className="card-header"),
        html.Div(graph, className="card-body research-chart-body"),
    ], className="card research-card")


def _indicator_selection_badges(
    pairs: Iterable[tuple[str, str]],
) -> list[Any]:
    pairs = list(pairs)
    if not pairs:
        return [html.Span("선택된 조합 없음", className="research-indicator-selection-empty")]
    return [
        html.Span(
            f"{ticker} · {INDICATOR_LABELS.get(column, column)}",
            className="research-indicator-selection-badge",
        )
        for ticker, column in pairs
    ]


def _toggle_indicator_matrix_values(
    triggered: dict[str, Any],
    row_values: Iterable[Iterable[str] | None],
    row_ids: Iterable[dict[str, str]],
    tickers: Iterable[str],
) -> list[list[str]]:
    """Toggle one matrix row or one panel-specific ticker column."""
    ticker_list = list(tickers)
    ids = list(row_ids)
    updated = [list(values or []) for values in row_values]
    if triggered.get("type") == "indicator-row-toggle":
        column = triggered.get("column")
        index = next(
            (position for position, row_id in enumerate(ids)
             if row_id.get("column") == column),
            None,
        )
        if index is None:
            raise PreventUpdate
        updated[index] = (
            [] if set(updated[index]) >= set(ticker_list) else ticker_list.copy()
        )
        return updated
    if triggered.get("type") == "indicator-column-toggle":
        ticker = triggered.get("ticker")
        panel = triggered.get("panel")
        indexes = [
            position for position, row_id in enumerate(ids)
            if indicator_panel(row_id.get("column", "")) == panel
        ]
        if not indexes or ticker not in ticker_list:
            raise PreventUpdate
        remove = all(ticker in updated[index] for index in indexes)
        for index in indexes:
            if remove:
                updated[index] = [
                    value for value in updated[index] if value != ticker
                ]
            elif ticker not in updated[index]:
                updated[index].append(ticker)
        return updated
    raise PreventUpdate


def strategy_name(result: dict[str, Any]) -> str:
    """Return the display name used by the existing Matplotlib chart."""
    return strategy_display_name(result["strategy"])


def _is_product_mapped_strategy(strategy: object) -> bool:
    """Return whether a strategy maps a model asset to an investable product."""
    mapping = getattr(strategy, "asset_mapping", None)
    if not isinstance(mapping, dict):
        return False
    return any(
        any(product != model_asset for product in products)
        for model_asset, products in mapping.items()
        if isinstance(products, dict)
    )


def _strategy_options(view: "ResearchViewModel") -> list[dict[str, Any]]:
    options = []
    for result in view.results:
        name = strategy_name(result)
        label = (
            html.Span(name, className="research-product-strategy-label")
            if _is_product_mapped_strategy(result["strategy"])
            else name
        )
        options.append({"label": label, "value": name})
    return options


def _resample_ohlc(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate QQQ OHLC rows to the requested display timeframe."""
    ohlc = frame[["Open", "High", "Low", "Close"]].dropna()
    if timeframe == "daily":
        return ohlc
    rule = {"weekly": "W-FRI", "monthly": "ME"}.get(timeframe)
    if rule is None:
        return ohlc.iloc[0:0]
    resampler = ohlc.resample(rule)
    aggregated = resampler.agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    }).dropna()
    # Plot each candle on its actual last trading day. Synthetic Friday or
    # month-end timestamps make unified hover jump to dates with no market row.
    last_trading_dates = pd.Series(ohlc.index, index=ohlc.index).resample(rule).last()
    aggregated.index = pd.DatetimeIndex(last_trading_dates.loc[aggregated.index])
    return aggregated


def _date_bounds(results: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [
        pd.Timestamp(date)
        for result in results
        for date in result["history"].index
    ]
    if not dates:
        return None, None
    return min(dates).date().isoformat(), max(dates).date().isoformat()


def _filtered_history(history: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    frame = history.copy()
    if start:
        frame = frame.loc[frame.index >= pd.Timestamp(start)]
    if end:
        frame = frame.loc[frame.index <= pd.Timestamp(end)]
    return frame


def _indexed_portfolio(history: pd.DataFrame) -> pd.Series:
    portfolio = history["Portfolio"].dropna()
    if portfolio.empty:
        return portfolio
    return portfolio / portfolio.iloc[0]


def _weight_frame(history: pd.DataFrame) -> pd.DataFrame:
    if "Weights" not in history:
        return pd.DataFrame(index=history.index)
    weights = history["Weights"].apply(lambda value: value if isinstance(value, dict) else {})
    return pd.DataFrame(weights.tolist(), index=history.index).fillna(0.0)


def _weight_change_frame(history: pd.DataFrame, max_points: int = 700) -> pd.DataFrame:
    """Downsample daily allocation drift while preserving material jumps."""
    frame = _weight_frame(history)
    if frame.empty:
        return frame
    changed = frame.ne(frame.shift()).any(axis=1)
    changed.iloc[0] = True
    changed.iloc[-1] = True
    compact = frame.loc[changed]
    if len(compact) <= max_points:
        return compact

    stride = max(1, (len(frame) + max_points - 1) // max_points)
    positions = set(range(0, len(frame), stride))
    material_jump = frame.diff().abs().max(axis=1).fillna(0.0) >= 0.005
    for position in range(1, len(frame)):
        if material_jump.iloc[position]:
            positions.update((position - 1, position))
    positions.update((0, len(frame) - 1))
    return frame.iloc[sorted(positions)]


def _allocation_display_frame(
    history: pd.DataFrame, strategy: Any, *, compact: bool = True,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Hide signal-only indexes and label their mapped trade products."""
    frame = _weight_change_frame(history) if compact else _weight_frame(history)
    labels: dict[str, str] = {
        ticker: PRODUCT_DISPLAY_NAMES.get(ticker, ticker)
        for ticker in frame.columns
    }
    mapping = getattr(strategy, "asset_mapping", {}) or {}
    for index_asset, product_mix in mapping.items():
        products = tuple(product_mix)
        is_replaced = index_asset not in products
        if is_replaced and index_asset in frame.columns:
            frame = frame.drop(columns=index_asset)
        for product in products:
            product_name = PRODUCT_DISPLAY_NAMES.get(product, product)
            labels[product] = (
                f"{product_name} · {index_asset} 계열"
                if is_replaced else product_name
            )
    return frame, labels


def _rebalance_change_text(event: dict[str, Any], strategy: Any) -> str:
    """Build an HTML hover table showing pre-trade and target weights."""
    before = event.get("PreWeights", {})
    target = event.get("Target", {})
    if not isinstance(before, dict):
        before = {}
    if not isinstance(target, dict) or not target:
        return "비중 변경 정보 없음"

    mapping = getattr(strategy, "asset_mapping", {}) or {}
    hidden_assets = {
        index_asset
        for index_asset, product_mix in mapping.items()
        if index_asset not in product_mix
    }
    labels = {
        ticker: PRODUCT_DISPLAY_NAMES.get(ticker, ticker)
        for ticker in target
    }
    for index_asset, product_mix in mapping.items():
        is_replaced = index_asset not in product_mix
        for product in product_mix:
            product_name = PRODUCT_DISPLAY_NAMES.get(product, product)
            labels[product] = (
                f"{product_name} · {index_asset} 계열"
                if is_replaced else product_name
            )

    tickers = [ticker for ticker in target if ticker not in hidden_assets]
    padded_labels = _padded_hover_labels(
        [labels.get(ticker, ticker) for ticker in tickers]
    )
    rows = ["종목별 비중 변경"]
    rows.extend(
        f"{padded_labels[labels.get(ticker, ticker)]}　"
        f"{_hover_values([float(before.get(ticker, 0.0)) * 100])[0]} → "
        f"{_hover_values([float(target[ticker]) * 100])[0]}"
        for ticker in tickers
    )
    execution_days = event.get("ExecutionDays")
    if execution_days:
        rows.append(f"분할 체결　{execution_days}거래일")
    return "<br>".join(rows)


def _combined_detail_hover_text(
    indexed: pd.Series,
    weights: pd.DataFrame,
    labels: dict[str, str],
    events_by_date: dict[pd.Timestamp, dict[str, Any]],
) -> list[str]:
    """Build one aligned hover panel for returns, weights, and rebalance targets."""
    display_names = [labels.get(ticker, ticker) for ticker in weights.columns]
    padded_labels = _padded_hover_labels(["누적 수익률", *display_names])
    hover_rows = []
    for date, indexed_value in indexed.items():
        event = events_by_date.get(pd.Timestamp(date))
        target = event.get("Target", {}) if event else {}
        before = event.get("PreWeights", {}) if event else {}
        if not isinstance(target, dict):
            target = {}
        if not isinstance(before, dict):
            before = {}
        rows = [
            f"{padded_labels['누적 수익률']}　"
            f"{_hover_values([(indexed_value - 1) * 100])[0]}"
        ]
        for ticker in weights.columns:
            name = labels.get(ticker, ticker)
            current_weight = (
                float(before.get(ticker, 0.0))
                if before else float(weights.at[date, ticker])
            )
            current_value = _hover_values([current_weight * 100])[0]
            row = f"{padded_labels[name]}　{current_value}"
            if event:
                target_value = _hover_values([float(target.get(ticker, 0.0)) * 100])[0]
                row += f"　→　{target_value}"
            rows.append(row)
        execution_days = event.get("ExecutionDays") if event else None
        if execution_days:
            rows.append(f"분할 체결　{execution_days}거래일")
        hover_rows.append("<br>".join(rows))
    return hover_rows


def _combined_detail_hover_payload(
    indexed: pd.Series,
    weights: pd.DataFrame,
    labels: dict[str, str],
    events_by_date: dict[pd.Timestamp, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return structured tooltip values so CSS, not spaces, aligns the columns."""
    payload = []
    for date, indexed_value in indexed.items():
        event = events_by_date.get(pd.Timestamp(date))
        target = event.get("Target", {}) if event else {}
        before = event.get("PreWeights", {}) if event else {}
        if not isinstance(target, dict):
            target = {}
        if not isinstance(before, dict):
            before = {}
        assets = []
        for ticker in weights.columns:
            current_weight = (
                float(before.get(ticker, 0.0))
                if before else float(weights.at[date, ticker])
            )
            assets.append({
                "name": labels.get(ticker, ticker),
                "current": f"{current_weight * 100:,.2f}%",
                "target": (
                    f"{float(target.get(ticker, 0.0)) * 100:,.2f}%"
                    if event else None
                ),
            })
        payload.append({
            "date": pd.Timestamp(date).strftime("%Y.%m.%d"),
            "return": f"{(float(indexed_value) - 1) * 100:,.2f}%",
            "assets": assets,
            "executionDays": event.get("ExecutionDays") if event else None,
        })
    return payload


@dataclass(frozen=True)
class ResearchViewModel:
    """Read-only projection of backtest results used by the web dashboard."""

    results: tuple[dict[str, Any], ...]
    _indicator_overlay_cache: dict[
        tuple[str, str, str], dict[str, Any]
    ] = field(default_factory=dict, init=False, repr=False, compare=False)

    @property
    def names(self) -> list[str]:
        return [strategy_name(result) for result in self.results]

    @cached_property
    def market_frames(self) -> dict[str, pd.DataFrame]:
        """Return one de-duplicated indicator frame per ticker."""
        frames: dict[str, pd.DataFrame] = {}
        for result in self.results:
            market_data = result.get("market_data")
            if isinstance(market_data, dict):
                for ticker, frame in market_data.items():
                    if ticker not in frames and isinstance(frame, pd.DataFrame):
                        # Completed result frames are read-only in this view; slicing
                        # below copies only the requested date window when needed.
                        frames[ticker] = frame
                continue
            if not isinstance(market_data, pd.DataFrame):
                continue
            strategy = result.get("strategy")
            tickers = getattr(strategy, "required_tickers", ()) or ()
            for ticker in tickers:
                if ticker in frames:
                    continue
                prefix = f"{ticker}_"
                columns = [
                    column for column in market_data.columns
                    if column.startswith(prefix)
                ]
                if columns:
                    frame = market_data[columns].copy()
                    frame.columns = [column[len(prefix):] for column in columns]
                    frames[ticker] = frame
        return frames

    def _indicator_overlay_data(
        self, selected_name: str | None, start=None, end=None,
    ) -> dict[str, Any] | None:
        """Cache strategy data that is unchanged when indicator pairs change."""
        if not selected_name:
            return None
        key = (selected_name, str(start or ""), str(end or ""))
        if key in self._indicator_overlay_cache:
            return self._indicator_overlay_cache[key]
        result = next(
            (item for item in self.results
             if strategy_name(item) == selected_name),
            None,
        )
        if result is None or "Portfolio" not in result["history"]:
            return None

        history = _filtered_history(result["history"], start, end)
        indexed = _indexed_portfolio(history)
        values = indexed * 100
        states = (
            history["StrategyState"].dropna().sort_index()
            if "StrategyState" in history else pd.Series(dtype=object)
        )
        weights, labels = _allocation_display_frame(
            history, result["strategy"], compact=False,
        )
        weights = weights.reindex(indexed.index).ffill().fillna(0.0)
        events = [
            (pd.Timestamp(event["ExecutionDate"]), event)
            for event in result.get("rebalances", [])
            if event.get("ExecutionDate") is not None
        ]
        if start:
            events = [
                (date, event) for date, event in events
                if date >= pd.Timestamp(start)
            ]
        if end:
            events = [
                (date, event) for date, event in events
                if date <= pd.Timestamp(end)
            ]
        details = {
            date: detail for date, detail in zip(
                indexed.index,
                _combined_detail_hover_payload(
                    indexed, weights, labels, dict(events),
                ),
            )
        }
        data = {
            "result": result,
            "values": values,
            "states": states,
            "events": events,
            "details": details,
        }
        if len(self._indicator_overlay_cache) >= 4:
            oldest_key = next(iter(self._indicator_overlay_cache))
            self._indicator_overlay_cache.pop(oldest_key)
        self._indicator_overlay_cache[key] = data
        return data

    def indicator_tooltip_data(
        self, selected_name: str | None, start=None, end=None,
    ) -> dict[str, dict[str, Any]]:
        """Return date-keyed strategy context independently of indicator traces."""
        data = self._indicator_overlay_data(selected_name, start, end)
        if data is None:
            return {}
        state_labels = {
            "BULL": "상승",
            "CAUTION": "주의",
            "BEAR": "하락",
            "RECOVERY": "회복",
        }
        states = data["states"]
        states_at_dates = (
            states.reindex(data["values"].index, method="ffill")
            if not states.empty else None
        )
        tooltip_data = {}
        for position, (date, detail) in enumerate(data["details"].items()):
            item = {"portfolio": detail}
            if states_at_dates is not None:
                state = states_at_dates.iloc[position]
                if pd.notna(state):
                    item["state"] = state_labels.get(str(state), str(state))
            tooltip_data[pd.Timestamp(date).strftime("%Y-%m-%d")] = item
        return tooltip_data

    @property
    def indicator_date_bounds(self) -> tuple[str | None, str | None]:
        indexes = [frame.index for frame in self.market_frames.values() if not frame.empty]
        if not indexes:
            return None, None
        return (
            min(pd.Timestamp(index.min()) for index in indexes).date().isoformat(),
            max(pd.Timestamp(index.max()) for index in indexes).date().isoformat(),
        )

    @property
    def date_bounds(self) -> tuple[str | None, str | None]:
        return _date_bounds(self.results)

    def selected_results(self, selected_names: Iterable[str] | None) -> list[dict[str, Any]]:
        selected = set(self.names if selected_names is None else selected_names)
        return [result for result in self.results if strategy_name(result) in selected]

    def performance_figure(self, selected_names=None, start=None, end=None) -> go.Figure:
        figure = go.Figure()
        selected_results = self.selected_results(selected_names)
        for result in selected_results:
            history = _filtered_history(result["history"], start, end)
            values = _indexed_portfolio(history)
            if values.empty:
                continue
            name = strategy_name(result)
            percentages = (values - 1) * 100
            figure.add_trace(go.Scatter(
                x=values.index, y=percentages, mode="lines",
                name=name,
                connectgaps=False,
                customdata=[{
                    "date": pd.Timestamp(date).strftime("%Y.%m.%d"),
                    "name": name,
                    "value": f"{float(value):,.2f}%",
                } for date, value in percentages.items()],
                hoverinfo="none",
            ))
        return _apply_detail_navigation(
            _apply_chart_style(figure, yaxis_title="수익률 (%)"),
            show_range_controls=False,
            show_range_slider=False,
            show_range_selector=True,
            separate_selector_row=True,
        )

    def drawdown_figure(self, selected_names=None, start=None, end=None) -> go.Figure:
        figure = go.Figure()
        selected_results = self.selected_results(selected_names)
        for result in selected_results:
            history = _filtered_history(result["history"], start, end)
            values = _indexed_portfolio(history)
            if values.empty:
                continue
            drawdown = (values / values.cummax() - 1) * 100
            name = strategy_name(result)
            figure.add_trace(go.Scatter(
                x=drawdown.index, y=drawdown, mode="lines",
                name=name,
                connectgaps=False,
                customdata=[{
                    "date": pd.Timestamp(date).strftime("%Y.%m.%d"),
                    "name": name,
                    "value": f"{float(value):,.2f}%",
                } for date, value in drawdown.items()],
                hoverinfo="none",
            ))
        return _apply_detail_navigation(
            _apply_chart_style(figure, yaxis_title="낙폭 (%)"),
            show_range_controls=False,
            show_range_slider=False,
            show_range_selector=True,
            separate_selector_row=True,
        )

    def total_return(self, selected_name: str | None, start=None, end=None) -> float | None:
        """Return the selected strategy's total return inside the analysis period."""
        result = next(
            (item for item in self.results if strategy_name(item) == selected_name),
            None,
        )
        if result is None:
            return None
        indexed = _indexed_portfolio(_filtered_history(result["history"], start, end))
        return None if indexed.empty else float(indexed.iloc[-1] - 1)

    def indicator_figure(
        self,
        selected_tickers: Iterable[str] | None,
        selected_columns: Iterable[str] | None,
        start=None,
        end=None,
        overlay_strategy: str | None = None,
        overlay_options: Iterable[str] | None = None,
        selected_pairs: Iterable[tuple[str, str]] | None = None,
        candle_timeframes: Iterable[str] | None = None,
    ) -> go.Figure:
        """Build synchronized price, oscillator, and risk research panels."""
        frames = self.market_frames
        if selected_pairs is None:
            pairs = [
                (ticker, column)
                for ticker in (selected_tickers or [])
                for column in (selected_columns or [])
            ]
        else:
            pairs = list(selected_pairs)
        pairs = list(dict.fromkeys(
            (ticker, column)
            for ticker, column in pairs
            if ticker in frames and indicator_panel(column) is not None
        ))
        tickers = list(dict.fromkeys(ticker for ticker, _ in pairs))
        columns = list(dict.fromkeys(column for _, column in pairs))
        overlay_data = self._indicator_overlay_data(
            overlay_strategy, start, end,
        )
        overlay_result = overlay_data["result"] if overlay_data else None
        has_strategy_return = overlay_data is not None
        candle_timeframes = list(dict.fromkeys(
            timeframe for timeframe in (candle_timeframes or [])
            if timeframe in {"daily", "weekly", "monthly"}
        ))
        has_qqq_candles = bool(candle_timeframes and "QQQ" in frames)
        panels = [
            panel for panel in PANEL_ORDER
            if any(indicator_panel(column) == panel for column in columns)
            or (panel == "price" and (has_strategy_return or has_qqq_candles))
        ]
        if not panels or (not tickers and not has_strategy_return and not has_qqq_candles):
            figure = _apply_chart_style(go.Figure())
            figure.add_annotation(
                text="종목과 지표를 선택하면 연구 그래프가 표시됩니다.",
                x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                font={"color": TV_MUTED, "size": 13},
            )
            figure.update_layout(height=420, hovermode="x unified")
            return figure

        row_heights = [{"price": 2.1, "oscillator": 1.35, "risk": 1.35}[panel] for panel in panels]
        figure = make_subplots(
            rows=len(panels), cols=1, shared_xaxes=True,
            vertical_spacing=0.055,
            row_heights=row_heights,
            subplot_titles=[PANEL_LABELS[panel] for panel in panels],
        )
        strategy_states = (
            overlay_data["states"] if overlay_data is not None
            else pd.Series(dtype=object)
        )
        strategy_values = (
            overlay_data["values"] if overlay_data is not None
            else pd.Series(dtype=float)
        )
        strategy_events = (
            overlay_data["events"] if overlay_data is not None else []
        )
        style_index = 0
        filtered_frames: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            frame = _filtered_history(frames[ticker], start, end)
            filtered_frames[ticker] = frame
            for column in columns:
                if (ticker, column) not in pairs:
                    continue
                panel = indicator_panel(column)
                if panel not in panels or column not in frame:
                    continue
                values = frame[column].dropna()
                if values.empty:
                    continue
                if indicator_is_indexed(column):
                    close = frame.get("Close", pd.Series(dtype=float)).dropna()
                    if close.empty or float(close.iloc[0]) == 0:
                        continue
                    values = values / float(close.iloc[0]) * 100
                row = panels.index(panel) + 1
                color = TV_COLORS[style_index % len(TV_COLORS)]
                dash = ("solid", "dash", "dot", "dashdot")[
                    (style_index // len(TV_COLORS)) % 4
                ]
                label = INDICATOR_LABELS.get(column, column)
                trace_name = f"{ticker} · {label}"
                figure.add_trace(go.Scattergl(
                    x=values.index,
                    y=values,
                    mode="lines",
                    name=trace_name,
                    line={"color": color, "width": 1.5, "dash": dash},
                    meta={"tooltipName": trace_name, "panel": panel},
                    hoverinfo="none",
                    connectgaps=False,
                ), row=row, col=1)
                style_index += 1

        if has_qqq_candles and "price" in panels:
            qqq = _filtered_history(frames["QQQ"], start, end)
            close = qqq.get("Close", pd.Series(dtype=float)).dropna()
            if not close.empty and float(close.iloc[0]) != 0:
                baseline = float(close.iloc[0])
                candle_labels = {"daily": "일봉", "weekly": "주봉", "monthly": "월봉"}
                price_row = panels.index("price") + 1
                for timeframe in candle_timeframes:
                    candles = _resample_ohlc(qqq, timeframe) / baseline * 100
                    if candles.empty:
                        continue
                    trace_name = f"QQQ · {candle_labels[timeframe]}"
                    figure.add_trace(go.Candlestick(
                        x=candles.index,
                        open=candles["Open"], high=candles["High"],
                        low=candles["Low"], close=candles["Close"],
                        name=trace_name,
                        increasing={
                            "line": {"color": "#F23645"},
                            "fillcolor": "rgba(242,54,69,.38)",
                        },
                        decreasing={
                            "line": {"color": "#2962FF"},
                            "fillcolor": "rgba(41,98,255,.38)",
                        },
                        whiskerwidth=.35,
                        hoverinfo="none",
                        meta={"tooltipName": trace_name, "panel": "price"},
                    ), row=price_row, col=1)

        if has_strategy_return and "price" in panels and not strategy_values.empty:
            figure.add_trace(go.Scattergl(
                x=strategy_values.index,
                y=strategy_values,
                mode="lines",
                name=overlay_strategy,
                line={"color": TV_TEXT, "width": 2.4},
                meta={
                    "tooltipName": overlay_strategy,
                    "panel": "price",
                    "isStrategySeries": True,
                },
                hoverinfo="none",
                connectgaps=False,
            ), row=panels.index("price") + 1, col=1)

        overlay = set(overlay_options or [])
        state_shapes = []
        if "states" in overlay and "price" in panels and overlay_strategy:
            result = overlay_result
            if result is not None and "StrategyState" in result["history"]:
                states = _filtered_history(result["history"], start, end)["StrategyState"].dropna()
                state_colors = {
                    "BULL": ("rgba(47,179,68,.18)", "rgba(47,179,68,.45)"),
                    "CAUTION": ("rgba(245,159,0,.22)", "rgba(245,159,0,.52)"),
                    "BEAR": ("rgba(214,57,57,.18)", "rgba(214,57,57,.46)"),
                    "RECOVERY": ("rgba(32,107,196,.18)", "rgba(32,107,196,.45)"),
                }
                if not states.empty:
                    run_start = states.index[0]
                    previous = states.iloc[0]
                    for position in range(1, len(states) + 1):
                        changed = position == len(states) or states.iloc[position] != previous
                        if changed:
                            run_end = states.index[min(position, len(states) - 1)]
                            fill, border = state_colors.get(
                                str(previous),
                                ("rgba(98,105,118,.12)", "rgba(98,105,118,.35)"),
                            )
                            state_shapes.append({
                                "type": "rect",
                                "x0": run_start,
                                "x1": run_end,
                                "xref": "x",
                                "y0": 0,
                                "y1": 1,
                                "yref": "y domain",
                                "fillcolor": fill,
                                "line": {"color": border, "width": .7},
                                "layer": "below",
                            })
                            if position < len(states):
                                run_start = states.index[position]
                                previous = states.iloc[position]
        if state_shapes:
            figure.update_layout(shapes=state_shapes)

        if "rebalances" in overlay and "price" in panels and overlay_strategy:
            result = overlay_result
            if result is not None:
                dates = [date for date, _ in strategy_events]
                if dates and not strategy_values.empty:
                    marker_values = strategy_values.reindex(dates, method="ffill")
                    figure.add_trace(go.Scattergl(
                        x=marker_values.index, y=marker_values,
                        mode="markers", name=f"{overlay_strategy} · 리밸런싱",
                        showlegend=False,
                        meta={"excludeTooltip": True, "panel": "price"},
                        marker={"symbol": "diamond", "size": 7, "color": TV_NEGATIVE},
                        hoverinfo="skip",
                    ), row=panels.index("price") + 1, col=1)

        figure = _apply_chart_style(
            figure,
            height=342 + 210 * len(panels),
        )
        figure.update_layout(hovermode="x unified")
        for annotation in figure.layout.annotations[:len(panels)]:
            annotation.update(x=0, xanchor="left")
        for row, panel in enumerate(panels, start=1):
            figure.update_yaxes(
                title_text="기준=100" if panel == "price" else "값",
                fixedrange=True,
                row=row, col=1,
            )
        return _apply_detail_navigation(
            figure,
            show_range_controls=True,
            slider_thickness=0.09,
            separate_selector_row=True,
            legend_columns=5,
            legend_plot_gap=42,
            selector_legend_gap=24,
            # Match the 520px performance-detail chart's 0.09 slider and
            # its year-label offset in physical pixels, regardless of panels.
            slider_height_px=0.09 * (520 - 96 - 78),
            slider_label_offset_px=0.235 * (520 - 96 - 78),
        )

    def allocation_figure(self, selected_name: str | None, start=None, end=None) -> go.Figure:
        result = next((item for item in self.results if strategy_name(item) == selected_name), None)
        figure = go.Figure()
        if result is None:
            return _apply_detail_navigation(
                _apply_chart_style(figure), show_range_controls=False
            )
        history = _filtered_history(result["history"], start, end)
        weights, labels = _allocation_display_frame(history, result["strategy"])
        display_names = [labels.get(ticker, ticker) for ticker in weights.columns]
        padded_labels = _padded_hover_labels(display_names)
        for ticker, values in weights.items():
            name = labels.get(ticker, ticker)
            percentages = values * 100
            figure.add_trace(go.Scatter(
                x=values.index, y=percentages, stackgroup="allocation", mode="lines",
                name=name,
                customdata=_hover_values(percentages),
                hovertemplate=f"{padded_labels[name]}　%{{customdata}}<extra></extra>",
            ))
        return _apply_detail_navigation(
            _apply_chart_style(figure, yaxis_title="비중 (%)", yaxis_range=[0, 100]),
            show_range_controls=False,
        )

    def rebalance_figure(self, selected_name: str | None, start=None, end=None) -> go.Figure:
        result = next((item for item in self.results if strategy_name(item) == selected_name), None)
        figure = go.Figure()
        if result is None:
            return _apply_detail_navigation(
                _apply_chart_style(figure), show_range_controls=True
            )
        history = _filtered_history(result["history"], start, end)
        values = _indexed_portfolio(history)
        percentages = (values - 1) * 100
        figure.add_trace(go.Scatter(
            x=values.index, y=percentages, mode="lines", name="누적 수익률",
            customdata=_hover_values(percentages),
            hovertemplate="누적 수익률　%{customdata}<extra></extra>",
        ))
        events = [
            (pd.Timestamp(event.get("ExecutionDate") or event["Date"]), event)
            for event in result.get("rebalances", [])
            if event.get("Date")
        ]
        if start:
            events = [(date, event) for date, event in events if date >= pd.Timestamp(start)]
        if end:
            events = [(date, event) for date, event in events if date <= pd.Timestamp(end)]
        points = (values - 1).reindex([date for date, _ in events]).dropna() * 100
        events_by_date = {date: event for date, event in events}
        hover_text = [
            _rebalance_change_text(events_by_date[date], result["strategy"])
            for date in points.index
        ]
        figure.add_trace(go.Scatter(
            x=points.index, y=points, mode="markers", name="리밸런싱",
            text=hover_text,
            hovertemplate="%{text}<extra>리밸런싱</extra>",
            marker={"symbol": "diamond", "size": 9, "color": TV_NEGATIVE, "line": {"color": "#FFFFFF", "width": 1}},
        ))
        return _apply_detail_navigation(
            _apply_chart_style(figure, yaxis_title="수익률 (%)"),
            show_range_controls=True,
        )

    def combined_detail_figure(
        self, selected_name: str | None, start=None, end=None,
    ) -> go.Figure:
        """Split cumulative return by each asset's current allocation."""
        result = next(
            (item for item in self.results if strategy_name(item) == selected_name),
            None,
        )
        figure = go.Figure()
        if result is None:
            return _apply_detail_navigation(
                _apply_chart_style(figure), show_range_controls=True
            )

        history = _filtered_history(result["history"], start, end)
        indexed = _indexed_portfolio(history)
        cumulative_return = (indexed - 1) * 100
        weights, labels = _allocation_display_frame(
            history, result["strategy"], compact=False
        )
        weights = weights.reindex(cumulative_return.index).ffill().fillna(0.0)
        for ticker, allocation in weights.items():
            name = labels.get(ticker, ticker)
            component = allocation * cumulative_return
            figure.add_trace(go.Scatter(
                x=component.index,
                y=component,
                mode="lines",
                stackgroup="portfolio-return",
                name=name,
                line={"width": 0.8},
                hoverinfo="skip",
            ))

        events = [
            (pd.Timestamp(event.get("ExecutionDate") or event["Date"]), event)
            for event in result.get("rebalances", [])
            if event.get("Date")
        ]
        if start:
            events = [(date, event) for date, event in events if date >= pd.Timestamp(start)]
        if end:
            events = [(date, event) for date, event in events if date <= pd.Timestamp(end)]
        points = cumulative_return.reindex([date for date, _ in events]).dropna()
        events_by_date = {date: event for date, event in events}
        hover_payload = _combined_detail_hover_payload(
            indexed, weights, labels, events_by_date
        )
        figure.add_trace(go.Scatter(
            x=cumulative_return.index,
            y=cumulative_return,
            mode="lines",
            name="누적 수익률",
            showlegend=False,
            customdata=hover_payload,
            line={"color": TV_TEXT, "width": 2.4},
            hoverinfo="none",
        ))
        figure.add_trace(go.Scatter(
            x=points.index,
            y=points,
            mode="markers",
            name="리밸런싱",
            text=[
                _rebalance_change_text(events_by_date[date], result["strategy"])
                for date in points.index
            ],
            hoverinfo="skip",
            marker={
                "symbol": "diamond", "size": 9, "color": TV_NEGATIVE,
                "line": {"color": "#FFFFFF", "width": 1},
            },
        ))
        figure = _apply_detail_navigation(
            _apply_chart_style(
                figure, yaxis_title="누적 수익률 (%)"
            ),
            show_range_controls=True,
        )
        figure.update_layout(hovermode="x unified")
        figure.update_xaxes(
            unifiedhovertitle={"text": "%{x|%Y.%m.%d}"}
        )
        return figure

    def summary_rows(
        self, selected_names: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        for result in self.selected_results(selected_names):
            row = {"Strategy": strategy_name(result)}
            summary = result.get("summary", {})
            total_return = self.total_return(strategy_name(result))
            total_return_added = False
            for key, value in summary.items():
                if key == "Start":
                    continue
                if key in {"End", "TotalReturn"}:
                    if not total_return_added:
                        row["TotalReturn"] = total_return
                        total_return_added = True
                    continue
                if key in {"StartDate", "EndDate"}:
                    timestamp = pd.Timestamp(value) if value is not None else pd.NaT
                    row[key] = (
                        None if pd.isna(timestamp)
                        else timestamp.strftime("%Y-%m-%d")
                    )
                    continue
                row[key] = value
            if not total_return_added:
                row["TotalReturn"] = total_return
            rows.append(row)
        return rows


def _indicator_layout_configuration(
    view: ResearchViewModel,
    selected_pairs: Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the ticker/indicator controls for one published result snapshot."""
    market_frames = view.market_frames
    indicator_tickers = list(market_frames)
    indicator_start, indicator_end = view.indicator_date_bounds
    start, end = view.date_bounds
    indicator_start = indicator_start or start
    indicator_end = indicator_end or end
    available_columns = {
        column for frame in market_frames.values() for column in frame.columns
    }
    panel_options = {
        panel: [
            option for option in indicator_options(panel)
            if option["value"] in available_columns
        ]
        for panel in PANEL_ORDER
    }
    default_tickers = (
        ["QQQ"] if "QQQ" in indicator_tickers else indicator_tickers[:1]
    )
    default_columns = {
        "price": [
            column for column in ("Close", "EMA55", "EMA200")
            if column in available_columns
        ],
        "oscillator": [
            column for column in ("RSI14", "MACD")
            if column in available_columns
        ],
        "risk": [
            column for column in ("ROC252", "VOL60", "MDD252")
            if column in available_columns
        ],
    }
    if selected_pairs is None:
        active_pairs = [
            (ticker, column)
            for column in (
                default_columns["price"]
                + default_columns["oscillator"]
                + default_columns["risk"]
            )
            for ticker in default_tickers
        ]
    else:
        active_pairs = [
            (ticker, column)
            for ticker, column in selected_pairs
            if ticker in indicator_tickers and column in available_columns
        ]
    selected_by_column: dict[str, list[str]] = {}
    for ticker, column in active_pairs:
        selected_by_column.setdefault(column, []).append(ticker)

    panels = []
    for panel in PANEL_ORDER:
        header = html.Div([
            html.Div("지표 / 종목", className="research-indicator-matrix-corner"),
            *[
                html.Button(
                    ticker,
                    id={
                        "type": "indicator-column-toggle",
                        "panel": panel,
                        "ticker": ticker,
                    },
                    n_clicks=0,
                    className="research-indicator-column-toggle",
                    title=f"{ticker} 열 전체 선택 또는 해제",
                )
                for ticker in indicator_tickers
            ],
        ], className="research-indicator-matrix-header")
        rows_for_panel = []
        for option in panel_options[panel]:
            column = option["value"]
            rows_for_panel.append(html.Div([
                html.Button(
                    option["label"],
                    id={"type": "indicator-row-toggle", "column": column},
                    n_clicks=0,
                    className="research-indicator-row-toggle",
                    title=f"{option['label']} 행 전체 선택 또는 해제",
                ),
                dcc.Checklist(
                    id={"type": "indicator-matrix-row", "column": column},
                    options=[
                        {"label": ticker, "value": ticker}
                        for ticker in indicator_tickers
                    ],
                    value=selected_by_column.get(column, []),
                    inline=True,
                    persistence=True,
                    persistence_type="local",
                    className="research-indicator-matrix-checklist",
                ),
            ], className="research-indicator-matrix-row"))
        panels.append(html.Div(
            html.Div(
                [header, *rows_for_panel],
                className="research-indicator-matrix-table",
                style={"--indicator-ticker-count": max(len(indicator_tickers), 1)},
            ),
            id=f"research-indicator-matrix-{panel}",
            className="research-indicator-matrix-scroll",
            style={} if panel == "price" else {"display": "none"},
        ))
    return {
        "tickers": indicator_tickers,
        "start": indicator_start,
        "end": indicator_end,
        "pairs": active_pairs,
        "panels": panels,
    }


def _component_with_id(component: Any, component_id: str) -> Any | None:
    """Find a component in a generated Dash layout."""
    if getattr(component, "id", None) == component_id:
        return component
    children = getattr(component, "children", None)
    if children is None:
        return None
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        found = _component_with_id(child, component_id)
        if found is not None:
            return found
    return None


def create_research_app(
    results: Iterable[dict[str, Any]],
    *,
    result_store=None,
    _defer_initial_figures: bool = False,
) -> Dash:
    """Create a Dash application over already calculated backtest results."""
    initial_snapshot = StrategyResultSnapshot(tuple(results), version=1)
    view_cache: dict[int, ResearchViewModel] = {
        initial_snapshot.version: ResearchViewModel(initial_snapshot.results)
    }
    view_cache_lock = RLock()

    def current_snapshot(*, refresh: bool = False) -> StrategyResultSnapshot:
        if result_store is None:
            return initial_snapshot
        return (
            result_store.refresh_if_changed()
            if refresh else result_store.snapshot()
        )

    def current_view(snapshot=None) -> ResearchViewModel:
        snapshot = snapshot or current_snapshot()
        with view_cache_lock:
            cached = view_cache.get(snapshot.version)
            if cached is None:
                cached = ResearchViewModel(snapshot.results)
                view_cache.clear()
                view_cache[snapshot.version] = cached
            return cached

    view = current_view(initial_snapshot)
    names = view.names
    start, end = view.date_bounds
    rows = view.summary_rows()
    summary_labels = {
        "Strategy": "전략", "CAGR": "CAGR", "MDD": "MDD", "Volatility": "변동성",
        "Sharpe": "Sharpe", "Sortino": "Sortino", "Calmar": "Calmar",
        "TransactionCosts": "거래비용", "TotalReturn": "전체 수익률",
        "StartDate": "시작일", "EndDate": "종료일",
    }
    percent_columns = {"CAGR", "MDD", "Volatility", "TotalReturn"}
    date_columns = {"StartDate", "EndDate"}
    strategy_column_width = max(
        190,
        24 + max((_display_width(name) for name in names), default=0) * 7,
    )
    column_min_widths = {
        "CAGR": 78, "MDD": 82, "Volatility": 88,
        "Sharpe": 84, "Sortino": 84, "Calmar": 84,
        "TransactionCosts": 96, "TotalReturn": 104,
        "StartDate": 104, "EndDate": 104,
    }
    column_defs = []
    for key in (rows[0].keys() if rows else ["Strategy"]):
        column = {
            "headerName": summary_labels.get(key, key),
            "field": key,
            "minWidth": column_min_widths.get(key, 82),
            "cellClass": "research-grid-number",
        }
        if key in percent_columns:
            column["valueFormatter"] = {"function": "params.value == null ? '—' : d3.format('.2%')(params.value)"}
        elif key == "TransactionCosts":
            column["valueFormatter"] = {"function": "params.value == null ? '—' : d3.format(',.4f')(params.value)"}
        elif key in date_columns:
            column.update({"cellClass": "research-grid-date"})
            column["valueFormatter"] = {
                "function": "params.value == null || params.value === 'NaT' ? '—' : String(params.value).slice(0, 10).replaceAll('-', '.')"
            }
        elif key != "Strategy":
            column["valueFormatter"] = {"function": "params.value == null ? '—' : d3.format('.2f')(params.value)"}
        else:
            column.update({
                "width": strategy_column_width,
                "minWidth": strategy_column_width,
                "suppressSizeToFit": True,
                "pinned": "left",
                "cellClass": "research-grid-strategy",
            })
        column_defs.append(column)

    selected_summary = rows[0] if rows else {}
    selected_total_return = view.total_return(
        names[0] if names else None, start, end
    )
    indicator_configuration = _indicator_layout_configuration(view)
    indicator_tickers = indicator_configuration["tickers"]
    indicator_start = indicator_configuration["start"]
    indicator_end = indicator_configuration["end"]
    initial_indicator_pairs = indicator_configuration["pairs"]
    defer_initial_figures = _defer_initial_figures or result_store is not None
    if defer_initial_figures:
        initial_performance_figure = go.Figure()
        initial_drawdown_figure = go.Figure()
        initial_detail_figure = go.Figure()
        initial_indicator_figure = go.Figure()
    else:
        initial_performance_figure = view.performance_figure(names, start, end)
        initial_drawdown_figure = view.drawdown_figure(names, start, end)
        initial_detail_figure = view.combined_detail_figure(
            names[0] if names else None, start, end
        )
        initial_indicator_figure = view.indicator_figure(
            None,
            None,
            indicator_start,
            indicator_end,
            selected_pairs=initial_indicator_pairs,
        )
    indicator_matrix_panels = indicator_configuration["panels"]
    graph_config = {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    }
    detail_graph_config = {
        **graph_config,
        "doubleClick": "reset",
        "showAxisDragHandles": False,
        "showAxisRangeEntryBoxes": False,
        "modeBarButtonsToRemove": [
            "lasso2d", "select2d", "zoom2d", "zoomIn2d", "zoomOut2d",
            "autoScale2d", "pan2d",
        ],
    }
    app = Dash(
        __name__, title="Investment Strategy Research",
        assets_folder=str(Path(__file__).with_name("assets")),
        external_stylesheets=[TABLER_STYLESHEET, TABLER_ICONS_STYLESHEET],
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    )

    @app.server.after_request
    def prevent_research_snapshot_cache(response):
        if request.path in {"/", "/_dash-layout", "/_dash-dependencies"}:
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    metric_specs = [
        ("CAGR", "연환산 수익률", "trending-up", "percent", "research-kpi-cagr", "positive"),
        ("MDD", "최대 낙폭", "chart-arrows-vertical", "percent", "research-kpi-mdd", "negative"),
        ("Sharpe", "위험조정 성과", "scale", "number", "research-kpi-sharpe", "primary"),
        ("TotalReturn", "전체 수익률", "percentage", "percent", "research-kpi-total-return", "cyan"),
    ]
    metric_cards = [
        html.Div(html.Div([
            html.Div([
                html.Div(label, className="research-kpi-label"),
                html.Div(
                    _metric_value(
                        selected_total_return if key == "TotalReturn" else selected_summary.get(key),
                        kind,
                    ),
                    id=component_id,
                    className="research-kpi-value",
                ),
            ]),
            html.Span(_icon(icon), className=f"research-kpi-icon research-kpi-{color}"),
        ], className="card-body research-kpi-body"), className="card research-kpi-card")
        for key, label, icon, kind, component_id, color in metric_specs
    ]

    app.layout = html.Div([
        dcc.Location(id="research-location", refresh=False),
        dcc.Interval(
            id="research-reload-trigger",
            interval=250,
            n_intervals=0,
            max_intervals=1,
        ),
        dcc.Store(id="research-result-version", data=initial_snapshot.version),
        dcc.Store(id="research-theme", storage_type="local", data="light"),
        dcc.Store(id="research-detail-range", data={"autorange": True}),
        dcc.Store(id="research-indicator-tooltip-data", data={}),
        html.Main([
            html.Nav(html.Div([
                html.Div([
                    html.Span(_icon("chart-line"), className="research-brand-mark"),
                    html.Div([
                        html.Div("Investment Strategy", className="research-brand-name"),
                        html.Div("Quantitative research", className="research-brand-caption"),
                    ]),
                ], className="research-brand"),
                html.Div([
                    html.Span([html.Span(className="status-dot status-dot-animated bg-green"), "Read only"], className="badge bg-green-lt research-status"),
                    html.Button(html.I(id="research-theme-icon", className="ti ti-moon", **{"aria-hidden": "true"}),
                                id="research-theme-toggle", n_clicks=0,
                                className="btn btn-icon btn-ghost-secondary", title="다크 모드로 전환",
                                **{"aria-label": "화면 테마 전환"}),
                ], className="research-navbar-actions"),
            ], className="research-container"), className="navbar navbar-expand-md research-navbar"),

            html.Div([
                html.Header([
                    html.Div([
                        html.Div("BACKTEST DASHBOARD", className="research-eyebrow"),
                        html.H1("투자 전략 리서치", className="research-title"),
                        html.P("완료된 백테스트 결과를 한 화면에서 비교하고 분석합니다.", className="research-subtitle"),
                    ]),
                    html.Span(
                        f"{len(names)}개 전략",
                        id="research-strategy-count",
                        className="badge bg-blue-lt research-count",
                    ),
                ], className="research-page-header"),

                html.Div(
                    id="research-reload-error",
                    className="alert alert-danger research-reload-error",
                    style={"display": "none"},
                ),

                dcc.RadioItems(
                    id="research-view-mode",
                    options=[
                        {"label": "성과 분석", "value": "analysis"},
                        {"label": "지표 연구", "value": "indicators"},
                    ],
                    value="analysis",
                    inline=True,
                    persistence=True,
                    persistence_type="local",
                    className="research-view-switch",
                ),

                html.Div([

                html.Section(html.Div([
                    html.Div([
                        html.Label([_icon("calendar"), "분석 기간"], className="form-label"),
                        dcc.DatePickerRange(
                            id="research-date-range", min_date_allowed=start, max_date_allowed=end,
                            start_date=start, end_date=end, display_format="YYYY.MM.DD",
                            persistence=True, persistence_type="local",
                        ),
                    ], className="research-control"),
                    html.Div([
                        html.Label([_icon("adjustments-horizontal"), "비교 전략"], className="form-label"),
                        dcc.Checklist(
                            id="research-strategies",
                            options=_strategy_options(view),
                            value=names,
                            inline=True,
                            persistence=True,
                            persistence_type="local",
                            className="research-checklist",
                        ),
                    ], className="research-control research-strategy-control"),
                ], className="card-body research-toolbar-body"), className="card research-toolbar"),

                html.Div([
                    _chart_card(
                        "누적 수익률", "동일 시작점으로 정규화한 전략별 성과",
                        [dcc.Graph(
                            id="research-performance", config=detail_graph_config,
                            className="research-graph research-navigation-graph",
                            figure=initial_performance_figure,
                            clear_on_unhover=True,
                            style={"height": "450px", "minHeight": "450px", "width": "100%", "display": "block"},
                        ), dcc.Tooltip(
                            id="research-performance-tooltip",
                            className="research-chart-tooltip research-comparison-tooltip",
                            direction="right",
                            background_color="rgba(255,255,255,0.98)",
                            border_color="#DCE1E7",
                            zindex=1100,
                        )], "chart-line",
                    ),
                    _chart_card(
                        "낙폭 경로", "고점 대비 손실과 회복 구간 비교",
                        [dcc.Graph(
                            id="research-drawdown", config=detail_graph_config,
                            className="research-graph research-navigation-graph",
                            figure=initial_drawdown_figure,
                            clear_on_unhover=True,
                            style={"height": "450px", "minHeight": "450px", "width": "100%", "display": "block"},
                        ), dcc.Tooltip(
                            id="research-drawdown-tooltip",
                            className="research-chart-tooltip research-comparison-tooltip",
                            direction="right",
                            background_color="rgba(255,255,255,0.98)",
                            border_color="#DCE1E7",
                            zindex=1100,
                        )], "chart-area-line",
                    ),
                ], className="research-chart-grid"),

                html.Section([
                    html.Div([
                        html.Div([
                            html.H2("전략 상세", className="card-title"),
                            html.P("자산 비중과 실제 리밸런싱 시점을 확인합니다.", className="research-card-description"),
                        ]),
                        html.Div([
                            html.Label("상세 전략", className="visually-hidden"),
                            dcc.Dropdown(
                                id="research-detail-strategy", options=names,
                                value=names[0] if names else None, clearable=False,
                                className="research-detail-dropdown",
                            ),
                        ], className="research-detail-select"),
                    ], className="card-header research-detail-header"),
                    html.Section(
                        metric_cards,
                        className="research-kpi-grid research-detail-kpi-grid",
                        **{"aria-label": "선택한 상세 전략의 핵심 성과 지표"},
                    ),
                    html.Div([
                        html.Section([
                            dcc.Graph(
                                id="research-detail-graph", config=detail_graph_config,
                                className="research-graph research-detail-graph research-combined-detail-graph",
                                figure=initial_detail_figure,
                                clear_on_unhover=True,
                                style={"height": "520px", "minHeight": "520px", "width": "100%", "display": "block"},
                            ),
                            dcc.Tooltip(
                                id="research-detail-tooltip",
                                className="research-chart-tooltip research-detail-tooltip",
                                direction="right",
                                background_color="rgba(255,255,255,0.98)",
                                border_color="#DCE1E7",
                                zindex=1100,
                            ),
                        ], className="research-detail-chart-section"),
                    ], className="card-body research-detail-body research-detail-stack"),
                ], className="card research-card research-detail-card"),

                html.Section([
                    html.Div([
                        html.Div([
                            html.H2("성과 요약", className="card-title"),
                            html.P("열을 정렬하거나 너비를 조절해 전략을 비교할 수 있습니다.", className="research-card-description"),
                        ]),
                    ], className="card-header research-summary-header"),
                    html.Div(dag.AgGrid(
                        id="research-summary-grid",
                        rowData=rows,
                        columnDefs=column_defs,
                        defaultColDef={"sortable": True, "resizable": True, "suppressMovable": False},
                        columnSize="responsiveSizeToFit",
                        columnSizeOptions={"defaultMinWidth": 72},
                        dashGridOptions={
                            "domLayout": "autoHeight", "animateRows": False,
                            "rowHeight": 38, "headerHeight": 42,
                            "pagination": len(rows) > 20, "paginationPageSize": 20,
                        },
                        className="ag-theme-quartz research-grid",
                    ), className="card-body research-summary-body"),
                ], className="card research-card research-summary"),
                ], id="research-analysis-view"),

                html.Div([
                    html.Section([
                        html.Div([
                            html.Div([
                                html.H2("지표 연구", className="card-title"),
                                html.P(
                                    "종목과 지표를 조합해 전략 조건과 시장 국면을 탐색합니다.",
                                    className="research-card-description",
                                ),
                            ]),
                        ], className="card-header"),
                        html.Div([
                            html.Div([
                                html.Label("분석 기간", className="form-label"),
                                dcc.DatePickerRange(
                                    id="research-indicator-date-range",
                                    min_date_allowed=indicator_start,
                                    max_date_allowed=indicator_end,
                                    start_date=indicator_start,
                                    end_date=indicator_end,
                                    display_format="YYYY.MM.DD",
                                    persistence=True,
                                    persistence_type="local",
                                ),
                            ], className="research-indicator-control"),
                            html.Div([
                                html.Label("전략 표시", className="form-label"),
                                dcc.Dropdown(
                                    id="research-indicator-strategy",
                                    options=[
                                        {"label": "표시 안 함", "value": ""},
                                        *[{"label": name, "value": name} for name in names],
                                    ],
                                    value="",
                                    clearable=False,
                                    persistence=True,
                                    persistence_type="local",
                                    className="research-indicator-strategy",
                                ),
                            ], className="research-indicator-control"),
                            html.Div([
                                html.Label("전략 이벤트", className="form-label"),
                                dcc.Checklist(
                                    id="research-indicator-overlays",
                                    options=[
                                        {"label": "상태 구간", "value": "states"},
                                        {"label": "리밸런싱", "value": "rebalances"},
                                    ],
                                    value=["states", "rebalances"],
                                    inline=True,
                                    persistence=True,
                                    persistence_type="local",
                                    className="research-indicator-checklist",
                                    style={"display": "none"},
                                ),
                                html.Div(
                                    "전략을 선택하면 표시할 이벤트를 설정할 수 있습니다.",
                                    id="research-indicator-overlay-hint",
                                    className="research-indicator-overlay-hint",
                                ),
                            ], className="research-indicator-control"),
                        ], className="card-body research-indicator-toolbar"),
                    ], className="card research-card research-indicator-controls"),

                    html.Section([
                        html.Div([
                            html.Div([
                                html.H2("표시 지표", className="card-title"),
                                html.P(
                                    "선택한 종목과 지표 조합",
                                    className="research-card-description",
                                ),
                            ], className="research-indicator-selector-title"),
                            html.Div(
                                _indicator_selection_badges(initial_indicator_pairs),
                                id="research-indicator-selection-summary",
                                className="research-indicator-selection-summary",
                            ),
                        ], className="card-header research-indicator-selector-header"),
                        html.Details([
                            html.Summary([
                                html.Span(
                                    _icon("adjustments-horizontal"),
                                    className="research-indicator-editor-icon",
                                ),
                                html.Span("종목·지표 편집"),
                                html.Span(
                                    _icon("chevron-down"),
                                    className="research-indicator-editor-chevron",
                                ),
                            ], className="research-indicator-editor-summary"),
                            html.Div([
                                dcc.RadioItems(
                                    id="research-indicator-panel",
                                    options=[
                                        {"label": PANEL_LABELS[panel], "value": panel}
                                        for panel in PANEL_ORDER
                                    ],
                                    value="price",
                                    inline=True,
                                    persistence=True,
                                    persistence_type="local",
                                    className="research-indicator-panel-tabs",
                                ),
                            ], className="research-indicator-editor-toolbar"),
                            html.Div(
                                indicator_matrix_panels,
                                id="research-indicator-matrix-body",
                                className="research-indicator-matrix-body",
                            ),
                        ], className="research-indicator-editor", open=False),
                    ], className="card research-card research-indicator-selector"),

                    html.Section([
                        html.Div([
                            html.Div([
                                html.Label("QQQ 봉 표시", className="form-label"),
                                dcc.Checklist(
                                    id="research-indicator-candles",
                                    options=[
                                        {"label": "일봉", "value": "daily"},
                                        {"label": "주봉", "value": "weekly"},
                                        {"label": "월봉", "value": "monthly"},
                                    ],
                                    value=[], inline=True,
                                    persistence=True, persistence_type="local",
                                    className="research-indicator-checklist",
                                ),
                            ], className=(
                                "research-indicator-control "
                                "research-indicator-candle-control"
                            )),
                        ], className="card-header research-indicator-chart-header"),
                        dcc.Loading(
                            dcc.Graph(
                                id="research-indicator-graph",
                                config=detail_graph_config,
                                className="research-graph research-indicator-graph",
                                figure=initial_indicator_figure,
                                clear_on_unhover=True,
                                style={"width": "100%", "display": "block"},
                            ),
                            type="dot",
                        ),
                        dcc.Tooltip(
                            id="research-indicator-tooltip",
                            className="research-chart-tooltip research-comparison-tooltip",
                            direction="right",
                            background_color="rgba(255,255,255,0.98)",
                            border_color="#DCE1E7",
                            zindex=1100,
                        ),
                    ],
                        className="card research-card research-indicator-chart-card",
                    ),
                ], id="research-indicator-view", style={"display": "none"}),
            ], className="research-container research-content"),
        ], id="research-page", className="research-page"),
    ], className="research-app-shell")

    @app.callback(
        Output("research-result-version", "data"),
        Output("research-reload-error", "children"),
        Output("research-reload-error", "style"),
        Output("research-strategy-count", "children"),
        Output("research-date-range", "min_date_allowed"),
        Output("research-date-range", "max_date_allowed"),
        Output("research-date-range", "start_date"),
        Output("research-date-range", "end_date"),
        Output("research-strategies", "options"),
        Output("research-strategies", "value"),
        Output("research-detail-strategy", "options"),
        Output("research-detail-strategy", "value"),
        Output("research-summary-grid", "rowData"),
        Output("research-indicator-date-range", "min_date_allowed"),
        Output("research-indicator-date-range", "max_date_allowed"),
        Output("research-indicator-date-range", "start_date"),
        Output("research-indicator-date-range", "end_date"),
        Output("research-indicator-strategy", "options"),
        Output("research-indicator-strategy", "value"),
        Output("research-indicator-matrix-body", "children"),
        Input("research-location", "pathname"),
        Input("research-reload-trigger", "n_intervals"),
        State("research-strategies", "value"),
        State("research-detail-strategy", "value"),
        State("research-indicator-strategy", "value"),
        State("research-result-version", "data"),
        State({"type": "indicator-matrix-row", "column": ALL}, "value"),
        State({"type": "indicator-matrix-row", "column": ALL}, "id"),
    )
    def reload_strategy_results(
        _pathname,
        _reload_tick,
        selected_names,
        detail_name,
        indicator_strategy,
        _displayed_version,
        indicator_row_values,
        indicator_row_ids,
    ):
        snapshot = current_snapshot(refresh=True)
        active_view = current_view(snapshot)
        active_names = active_view.names
        active_start, active_end = active_view.date_bounds

        preserved_names = [
            name for name in (selected_names or []) if name in active_names
        ]
        if detail_name not in active_names:
            detail_name = active_names[0] if active_names else None
        if indicator_strategy not in active_names:
            indicator_strategy = ""

        selected_pairs = [
            (ticker, row_id["column"])
            for row_id, selected in zip(
                indicator_row_ids or [], indicator_row_values or []
            )
            for ticker in (selected or [])
        ]
        indicator_config = _indicator_layout_configuration(
            active_view, selected_pairs
        )
        error_children = []
        error_style = {"display": "none"}
        if snapshot.error:
            error_children = [
                html.Strong("전략 파일을 다시 불러오지 못했습니다. "),
                html.Span(snapshot.error),
                html.Div(
                    "마지막으로 정상 실행된 결과를 계속 표시합니다.",
                    className="research-reload-error-note",
                ),
            ]
            error_style = {}

        strategy_options = _strategy_options(active_view)
        indicator_options_for_strategy = [
            {"label": "표시 안 함", "value": ""},
            *strategy_options,
        ]
        return (
            snapshot.version,
            error_children,
            error_style,
            f"{len(active_names)}개 전략",
            active_start,
            active_end,
            active_start,
            active_end,
            strategy_options,
            preserved_names,
            strategy_options,
            detail_name,
            active_view.summary_rows(preserved_names),
            indicator_config["start"],
            indicator_config["end"],
            indicator_config["start"],
            indicator_config["end"],
            indicator_options_for_strategy,
            indicator_strategy,
            indicator_config["panels"],
        )

    app.clientside_callback(
        """
        function(mode) {
            const indicators = mode === "indicators";
            return [
                indicators ? {display: "none"} : {},
                indicators ? {} : {display: "none"}
            ];
        }
        """,
        Output("research-analysis-view", "style"),
        Output("research-indicator-view", "style"),
        Input("research-view-mode", "value"),
    )

    app.clientside_callback(
        """
        function(strategy) {
            const enabled = Boolean(strategy);
            return [
                enabled ? {} : {display: "none"},
                enabled ? {display: "none"} : {}
            ];
        }
        """,
        Output("research-indicator-overlays", "style"),
        Output("research-indicator-overlay-hint", "style"),
        Input("research-indicator-strategy", "value"),
    )

    app.clientside_callback(
        """
        function(panel) {
            return [
                panel === "price" ? {} : {display: "none"},
                panel === "oscillator" ? {} : {display: "none"},
                panel === "risk" ? {} : {display: "none"}
            ];
        }
        """,
        Output("research-indicator-matrix-price", "style"),
        Output("research-indicator-matrix-oscillator", "style"),
        Output("research-indicator-matrix-risk", "style"),
        Input("research-indicator-panel", "value"),
    )

    @app.callback(
        Output({"type": "indicator-matrix-row", "column": ALL}, "value"),
        Input({"type": "indicator-row-toggle", "column": ALL}, "n_clicks"),
        Input({"type": "indicator-column-toggle", "panel": ALL, "ticker": ALL}, "n_clicks"),
        State({"type": "indicator-matrix-row", "column": ALL}, "value"),
        State({"type": "indicator-matrix-row", "column": ALL}, "id"),
        prevent_initial_call=True,
    )
    def toggle_indicator_matrix(
        _row_clicks, _column_clicks, row_values, row_ids,
    ):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            raise PreventUpdate
        return _toggle_indicator_matrix_values(
            triggered, row_values, row_ids, list(current_view().market_frames),
        )

    @app.callback(
        Output("research-indicator-tooltip-data", "data"),
        Input("research-indicator-strategy", "value"),
        Input("research-indicator-date-range", "start_date"),
        Input("research-indicator-date-range", "end_date"),
        Input("research-result-version", "data"),
    )
    def update_indicator_tooltip_data(
        overlay_strategy, selected_start, selected_end, _version,
    ):
        return current_view().indicator_tooltip_data(
            overlay_strategy, selected_start, selected_end,
        )

    @app.callback(
        Output("research-indicator-graph", "figure"),
        Output("research-indicator-selection-summary", "children"),
        Input({"type": "indicator-matrix-row", "column": ALL}, "value"),
        Input("research-indicator-date-range", "start_date"),
        Input("research-indicator-date-range", "end_date"),
        Input("research-indicator-strategy", "value"),
        Input("research-indicator-overlays", "value"),
        Input("research-indicator-candles", "value"),
        Input("research-result-version", "data"),
        State({"type": "indicator-matrix-row", "column": ALL}, "id"),
    )
    def update_indicator_research(
        row_values,
        selected_start,
        selected_end,
        overlay_strategy,
        overlay_options,
        candle_timeframes,
        version,
        row_ids,
    ):
        selected_pairs = [
            (ticker, row_id["column"])
            for row_id, selected in zip(row_ids, row_values)
            for ticker in (selected or [])
        ]
        figure = current_view().indicator_figure(
            None,
            None,
            selected_start,
            selected_end,
            overlay_strategy,
            overlay_options,
            selected_pairs=selected_pairs,
            candle_timeframes=candle_timeframes,
        )
        figure.update_layout(
            datarevision=(
                f"indicators:{selected_pairs}:"
                f"{overlay_strategy}:"
                f"{','.join(overlay_options or [])}:"
                f"{','.join(candle_timeframes or [])}:{version}"
            ),
            uirevision=f"indicator-range:{selected_start}:{selected_end}",
        )
        return figure, _indicator_selection_badges(selected_pairs)

    @app.callback(
        Output("research-summary-grid", "rowData", allow_duplicate=True),
        Input("research-strategies", "value"),
        Input("research-result-version", "data"),
        prevent_initial_call=True,
    )
    def update_summary(selected_names, _version):
        return current_view().summary_rows(selected_names or [])

    @app.callback(
        Output("research-performance", "figure"),
        Input("research-strategies", "value"),
        Input("research-date-range", "start_date"),
        Input("research-date-range", "end_date"),
        Input("research-result-version", "data"),
    )
    def update_performance(
        selected_names, selected_start, selected_end, version,
    ):
        figure = current_view().performance_figure(
            selected_names, selected_start, selected_end
        )
        revision = (
            f"performance:{','.join(selected_names or [])}:"
            f"{selected_start}:{selected_end}:{version}"
        )
        figure.update_layout(datarevision=revision, uirevision=revision)
        return figure

    @app.callback(
        Output("research-drawdown", "figure"),
        Input("research-strategies", "value"),
        Input("research-date-range", "start_date"),
        Input("research-date-range", "end_date"),
        Input("research-result-version", "data"),
    )
    def update_drawdown(
        selected_names, selected_start, selected_end, version,
    ):
        figure = current_view().drawdown_figure(
            selected_names, selected_start, selected_end
        )
        revision = (
            f"drawdown:{','.join(selected_names or [])}:"
            f"{selected_start}:{selected_end}:{version}"
        )
        figure.update_layout(datarevision=revision, uirevision=revision)
        return figure

    @app.callback(
        Output("research-detail-graph", "figure"),
        Output("research-kpi-cagr", "children"),
        Output("research-kpi-mdd", "children"),
        Output("research-kpi-sharpe", "children"),
        Output("research-kpi-total-return", "children"),
        Input("research-detail-strategy", "value"),
        Input("research-date-range", "start_date"),
        Input("research-date-range", "end_date"),
        Input("research-result-version", "data"),
        State("research-detail-range", "data"),
    )
    def update_detail(
        selected_name,
        selected_start,
        selected_end,
        version,
        stored_range,
    ):
        active_view = current_view()
        summary = next(
            (
                row for row in active_view.summary_rows()
                if row["Strategy"] == selected_name
            ),
            {},
        )
        detail_figure = active_view.combined_detail_figure(
            selected_name, selected_start, selected_end
        )
        revision = f"{selected_name}:{selected_start}:{selected_end}:{version}"
        detail_figure.update_layout(datarevision=revision, uirevision=revision)
        _apply_stored_detail_range(detail_figure, stored_range)
        bounded_range = _bounded_detail_range(detail_figure, stored_range)
        visible_relayout = (
            {"xaxis.range": bounded_range["range"]}
            if bounded_range and bounded_range.get("range")
            else {"xaxis.autorange": True}
        )
        y_range = _visible_rebalance_y_range(
            detail_figure.to_plotly_json(), visible_relayout
        )
        if y_range is not None:
            detail_figure.layout.yaxis.update(range=y_range, autorange=False)
        return (
            detail_figure,
            _metric_value(summary.get("CAGR"), "percent"),
            _metric_value(summary.get("MDD"), "percent"),
            _metric_value(summary.get("Sharpe"), "number"),
            _metric_value(
                active_view.total_return(
                    selected_name, selected_start, selected_end
                ),
                "percent",
            ),
        )

    @app.callback(
        Output("research-detail-range", "data"),
        Output("research-detail-graph", "figure", allow_duplicate=True),
        Input("research-detail-graph", "relayoutData"),
        State("research-detail-graph", "figure"),
        prevent_initial_call=True,
    )
    def update_detail_visible_range(relayout_data, detail_figure):
        range_state = _bounded_detail_range(
            detail_figure, _detail_range_state(relayout_data)
        )
        if range_state is None:
            raise PreventUpdate
        patched = Patch()
        axis_names = [
            name for name in detail_figure.get("layout", {})
            if name.startswith("xaxis")
        ] or ["xaxis"]
        if range_state.get("range"):
            for axis_name in axis_names:
                patched["layout"][axis_name]["autorange"] = False
                patched["layout"][axis_name]["range"] = range_state["range"]
            normalized_relayout = {"xaxis.range": range_state["range"]}
        else:
            for axis_name in axis_names:
                patched["layout"][axis_name]["autorange"] = True
            normalized_relayout = {"xaxis.autorange": True}
        y_range = _visible_rebalance_y_range(detail_figure, normalized_relayout)
        if y_range is not None:
            patched["layout"]["yaxis"]["autorange"] = False
            patched["layout"]["yaxis"]["range"] = y_range
        return range_state, patched

    def register_bounded_navigation(graph_id: str) -> None:
        @app.callback(
            Output(graph_id, "figure", allow_duplicate=True),
            Input(graph_id, "relayoutData"),
            State(graph_id, "figure"),
            prevent_initial_call=True,
        )
        def keep_visible_range_inside_data(relayout_data, figure):
            range_state = _bounded_detail_range(
                figure, _detail_range_state(relayout_data)
            )
            if range_state is None:
                raise PreventUpdate
            patched = Patch()
            axis_names = [
                name for name in figure.get("layout", {})
                if name.startswith("xaxis")
            ] or ["xaxis"]
            if range_state.get("range"):
                for axis_name in axis_names:
                    patched["layout"][axis_name]["autorange"] = False
                    patched["layout"][axis_name]["range"] = range_state["range"]
                if graph_id == "research-indicator-graph":
                    for axis_name, y_range in _visible_indicator_y_ranges(
                        figure, range_state["range"]
                    ).items():
                        patched["layout"][axis_name]["autorange"] = False
                        patched["layout"][axis_name]["range"] = y_range
            else:
                for axis_name in axis_names:
                    patched["layout"][axis_name]["autorange"] = True
                if graph_id == "research-indicator-graph":
                    for axis_name in figure.get("layout", {}):
                        if axis_name.startswith("yaxis"):
                            patched["layout"][axis_name]["autorange"] = True
            return patched

    for bounded_graph_id in (
        "research-performance",
        "research-drawdown",
        "research-indicator-graph",
    ):
        register_bounded_navigation(bounded_graph_id)

    comparison_tooltip_script = """
        function(hoverData, graphId, figure, indicatorTooltipData) {
            const noUpdate = window.dash_clientside.no_update;
            if (!hoverData || !hoverData.points || !hoverData.points.length) {
                return [false, noUpdate, noUpdate, noUpdate];
            }
            const isIndicatorGraph = graphId === "research-indicator-graph";
            const traces = figure && figure.data ? figure.data : [];
            const traceFor = point => traces[point.curveNumber] || {};
            const points = hoverData.points.filter(
                item => isIndicatorGraph
                    ? Boolean(traceFor(item).meta && !traceFor(item).meta.excludeTooltip)
                    : Boolean(item.customdata && item.customdata.name)
            );
            if (!points.length) {
                return [false, noUpdate, noUpdate, noUpdate];
            }
            const component = (type, className, children) => ({
                namespace: "dash_html_components",
                type: type,
                props: {className: className, children: children}
            });
            const span = (className, value) => component("Span", className, value || "");
            const formatNumber = value => {
                const numeric = Number(value);
                return Number.isFinite(numeric) ? numeric.toLocaleString("en-US", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                }) : "—";
            };
            const formatIndicatorValue = (point, trace) => {
                if (trace.type !== "candlestick") return formatNumber(point.y);
                return `시 ${formatNumber(point.open)} · ` +
                    `고 ${formatNumber(point.high)} · ` +
                    `저 ${formatNumber(point.low)} · ` +
                    `종 ${formatNumber(point.close)}`;
            };
            const row = (label, value, target, expanded) => {
                const cells = [
                    span("research-custom-tooltip-label", label),
                    span("research-custom-tooltip-value", value)
                ];
                if (expanded) {
                    cells.push(span("research-custom-tooltip-arrow", target ? "→" : ""));
                    cells.push(span("research-custom-tooltip-target", target || ""));
                }
                return component(
                    "Div", "research-custom-tooltip-row research-comparison-tooltip-row",
                    cells
                );
            };
            const dateKey = isIndicatorGraph
                ? String(points[0].x).slice(0, 10) : null;
            const dateText = isIndicatorGraph
                ? dateKey.replaceAll("-", ".") : points[0].customdata.date;
            const content = [
                component("Div", "research-custom-tooltip-date", dateText)
            ];
            const hoveredPanel = isIndicatorGraph
                ? (traceFor(points[0]).meta || {}).panel : null;
            const strategyContext = isIndicatorGraph && indicatorTooltipData
                ? indicatorTooltipData[dateKey] : null;
            const portfolio = hoveredPanel === "price" && strategyContext
                ? strategyContext.portfolio : null;
            const hasTargets = portfolio
                ? portfolio.assets.some(asset => asset.target) : false;
            const rows = [];
            if (portfolio) {
                const returnLabel = strategyContext.state
                    ? `누적 수익률 (${strategyContext.state})`
                    : "누적 수익률";
                rows.push(row(returnLabel, portfolio.return, null, hasTargets));
                rows.push(component("Div", "research-custom-tooltip-separator", ""));
                portfolio.assets.forEach(asset => rows.push(
                    row(asset.name, asset.current, asset.target, hasTargets)
                ));
                if (portfolio.executionDays) {
                    rows.push(component(
                        "Div",
                        "research-custom-tooltip-footer research-indicator-tooltip-grid-footer",
                        `분할 체결 ${portfolio.executionDays}거래일`
                    ));
                }
                rows.push(component(
                    "Div", "research-indicator-tooltip-section-separator", ""
                ));
            }
            const indicatorRows = points
                .filter(point => !isIndicatorGraph ||
                    !(traceFor(point).meta || {}).isStrategySeries)
                .map(point => row(
                    isIndicatorGraph
                        ? (traceFor(point).meta || {}).tooltipName
                        : point.customdata.name,
                    isIndicatorGraph
                        ? formatIndicatorValue(point, traceFor(point))
                        : point.customdata.value,
                    null,
                    hasTargets
                ));
            rows.push(...indicatorRows);
            if (rows.length) {
                content.push(component(
                    "Div",
                    "research-custom-tooltip-grid research-comparison-tooltip-grid" +
                        (hasTargets ? " research-detail-tooltip-grid-with-targets" : ""),
                    rows
                ));
            }
            const children = component(
                "Div", "research-custom-tooltip-card research-comparison-tooltip-card", content
            );
            const pointBoxes = points
                .map(point => point.bbox)
                .filter(box => box && Number.isFinite(box.x0) &&
                    Number.isFinite(box.x1) && Number.isFinite(box.y0) &&
                    Number.isFinite(box.y1));
            const bbox = pointBoxes.length ? {
                x0: Math.min(...pointBoxes.map(box => box.x0)),
                x1: Math.max(...pointBoxes.map(box => box.x1)),
                y0: Math.min(...pointBoxes.map(box => box.y0)),
                y1: Math.max(...pointBoxes.map(box => box.y1))
            } : points[0].bbox;
            const graph = document.getElementById(graphId);
            const graphBounds = graph ? graph.getBoundingClientRect() : null;
            const plot = graph ? graph.querySelector(".nsewdrag") : null;
            const plotBounds = plot ? plot.getBoundingClientRect() : null;
            const graphWidth = graphBounds ? graphBounds.width : 0;
            const graphHeight = graphBounds ? graphBounds.height : 0;
            const anchorX = bbox ? (bbox.x0 + bbox.x1) / 2 : 0;
            const isPathChart = graphId === "research-performance" ||
                graphId === "research-drawdown";
            let direction;
            if (isPathChart && bbox && graphHeight) {
                // Anchor beyond the complete group of hovered lines. This puts the
                // card in the larger empty vertical region instead of over a path.
                const controlSpace = plotBounds && graphBounds
                    ? Math.max(0, plotBounds.top - graphBounds.top) : 96;
                const axisSpace = plotBounds && graphBounds
                    ? Math.max(0, graphBounds.bottom - plotBounds.bottom) : 50;
                const spaceAbove = Math.max(0, bbox.y0 - controlSpace);
                const spaceBelow = Math.max(0, graphHeight - axisSpace - bbox.y1);
                direction = spaceAbove >= spaceBelow ? "top" : "bottom";
            } else {
                direction = graphWidth && anchorX > graphWidth / 2
                    ? "left" : "right";
            }
            return [true, bbox, children, direction];
        }
    """
    for graph_id, tooltip_id in (
        ("research-performance", "research-performance-tooltip"),
        ("research-drawdown", "research-drawdown-tooltip"),
        ("research-indicator-graph", "research-indicator-tooltip"),
    ):
        app.clientside_callback(
            comparison_tooltip_script,
            Output(tooltip_id, "show"),
            Output(tooltip_id, "bbox"),
            Output(tooltip_id, "children"),
            Output(tooltip_id, "direction"),
            Input(graph_id, "hoverData"),
            State(graph_id, "id"),
            State(graph_id, "figure"),
            State("research-indicator-tooltip-data", "data"),
        )

    app.clientside_callback(
        """
        function(hoverData, graphId) {
            const noUpdate = window.dash_clientside.no_update;
            if (!hoverData || !hoverData.points || !hoverData.points.length) {
                return [false, noUpdate, noUpdate, noUpdate];
            }
            const point = hoverData.points.find(
                item => item.customdata && item.customdata.assets
            );
            if (!point) {
                return [false, noUpdate, noUpdate, noUpdate];
            }
            const data = point.customdata;
            const component = (type, className, children) => ({
                namespace: "dash_html_components",
                type: type,
                props: {className: className, children: children}
            });
            const span = (className, value) => component("Span", className, value || "");
            const row = (label, value, target, expanded, isReturn) => {
                const cells = [
                    span("research-custom-tooltip-label", label),
                    span("research-custom-tooltip-value", value)
                ];
                if (expanded) {
                    cells.push(span("research-custom-tooltip-arrow", target ? "→" : ""));
                    cells.push(span("research-custom-tooltip-target", target || ""));
                }
                return component(
                    "Div",
                    "research-custom-tooltip-row research-detail-tooltip-row" +
                        (expanded ? " research-custom-tooltip-row-with-targets" : "") +
                        (isReturn ? " research-custom-tooltip-return-row" : ""),
                    cells
                );
            };
            const hasTargets = data.assets.some(asset => asset.target);
            const rows = [
                row("누적 수익률", data.return, null, hasTargets, true),
                component("Div", "research-custom-tooltip-separator", "")
            ];
            data.assets.forEach(asset => rows.push(
                row(asset.name, asset.current, asset.target, hasTargets, false)
            ));
            const children = [
                component("Div", "research-custom-tooltip-date", data.date),
                component(
                    "Div",
                    "research-custom-tooltip-grid research-detail-tooltip-grid" +
                        (hasTargets ? " research-detail-tooltip-grid-with-targets" : ""),
                    rows
                )
            ];
            if (data.executionDays) {
                children.push(component(
                    "Div", "research-custom-tooltip-footer",
                    `분할 체결 ${data.executionDays}거래일`
                ));
            }
            const graph = document.getElementById(graphId);
            const graphWidth = graph ? graph.getBoundingClientRect().width : 0;
            const anchorX = point.bbox ? (point.bbox.x0 + point.bbox.x1) / 2 : 0;
            const direction = graphWidth && anchorX > graphWidth / 2
                ? "left" : "right";
            return [
                true,
                point.bbox,
                component("Div", "research-custom-tooltip-card research-detail-tooltip-card", children),
                direction
            ];
        }
        """,
        Output("research-detail-tooltip", "show"),
        Output("research-detail-tooltip", "bbox"),
        Output("research-detail-tooltip", "children"),
        Output("research-detail-tooltip", "direction"),
        Input("research-detail-graph", "hoverData"),
        State("research-detail-graph", "id"),
    )

    app.clientside_callback(
        """
        function(nClicks, currentTheme) {
            const current = currentTheme || "light";
            const next = nClicks ? (current === "dark" ? "light" : "dark") : current;
            const pageClass = next === "dark" ? "research-page research-theme-dark" : "research-page";
            const iconClass = next === "dark" ? "ti ti-sun" : "ti ti-moon";
            const title = next === "dark" ? "라이트 모드로 전환" : "다크 모드로 전환";
            return [pageClass, iconClass, title, next];
        }
        """,
        Output("research-page", "className"),
        Output("research-theme-icon", "className"),
        Output("research-theme-toggle", "title"),
        Output("research-theme", "data"),
        Input("research-theme-toggle", "n_clicks"),
        State("research-theme", "data"),
    )

    if result_store is not None:
        initial_layout = app.layout
        published_layouts: dict[tuple[int, str | None], Any] = {
            (initial_snapshot.version, None): initial_layout
        }

        def serve_current_layout():
            snapshot = current_snapshot(refresh=True)
            cache_key = (snapshot.version, snapshot.error)
            with view_cache_lock:
                published = published_layouts.get(cache_key)
            if published is not None:
                return published

            # Layout generation is deliberately separated from the live result
            # store so this nested app cannot trigger another strategy reload.
            published = create_research_app(
                snapshot.results, _defer_initial_figures=True
            ).layout
            version_store = _component_with_id(
                published, "research-result-version"
            )
            if version_store is not None:
                version_store.data = snapshot.version
            if snapshot.error:
                error_box = _component_with_id(
                    published, "research-reload-error"
                )
                if error_box is not None:
                    error_box.children = [
                        html.Strong("전략 파일을 다시 불러오지 못했습니다. "),
                        html.Span(snapshot.error),
                        html.Div(
                            "마지막으로 정상 실행된 결과를 계속 표시합니다.",
                            className="research-reload-error-note",
                        ),
                    ]
                    error_box.style = {}
            with view_cache_lock:
                published_layouts.clear()
                published_layouts[cache_key] = published
            return published

        app.layout = serve_current_layout

    return app


def run_research_web(
    results: Iterable[dict[str, Any]],
    host="127.0.0.1",
    port=8050,
    *,
    result_store=None,
) -> None:
    """Run the local research dashboard after a backtest has completed."""
    create_research_app(results, result_store=result_store).run(
        host=host, port=port, debug=False
    )
