import unittest
from pathlib import Path

import pandas as pd

from main import parse_args
from pension_strategies import KodexNasdaqAllocationStrategy
from research_web import (
    ResearchViewModel,
    _apply_stored_detail_range,
    _detail_range_state,
    _toggle_indicator_matrix_values,
    _visible_indicator_y_ranges,
    _visible_rebalance_y_range,
    _weight_change_frame,
    create_research_app,
)
from strategy_runtime import StrategyResultSnapshot


class AlphaStrategy:
    pass


class BetaStrategy:
    pass


class GammaStrategy:
    pass


def make_result(strategy, values, rebalances=()):
    index = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    market_frame = pd.DataFrame({
        "Close": [100.0, 102.0, 101.0],
        "EMA55": [99.0, 100.0, 100.5],
        "EMA200": [95.0, 95.2, 95.4],
        "RSI14": [50.0, 58.0, 54.0],
        "MACD": [0.0, 0.4, 0.2],
        "ROC252": [10.0, 11.0, 10.5],
        "VOL60": [0.2, 0.21, 0.205],
        "MDD252": [-0.1, -0.08, -0.09],
    }, index=index)
    return {
        "strategy": strategy,
        "history": pd.DataFrame({
            "Portfolio": values,
            "Weights": [
                {"QQQ": 0.7, "BIL": 0.3},
                {"QQQ": 0.75, "BIL": 0.25},
                {"QQQ": 0.8, "BIL": 0.2},
            ],
        }, index=index),
        "summary": {
            "CAGR": 0.12, "MDD": -0.08, "Sharpe": 1.23456,
            "TransactionCosts": 0.012345, "Start": 100.0,
            "End": 121234.5678,
            "StartDate": pd.Timestamp("2024-01-01"),
            "EndDate": pd.Timestamp("2024-01-03"),
        },
        "rebalances": list(rebalances),
        "market_data": {"QQQ": market_frame},
    }


def find_component(component, component_id):
    if getattr(component, "id", None) == component_id:
        return component
    children = getattr(component, "children", None)
    if children is None:
        return None
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        found = find_component(child, component_id)
        if found is not None:
            return found
    return None


def find_component_by_class(component, class_name):
    if getattr(component, "className", None) == class_name:
        return component
    children = getattr(component, "children", None)
    if children is None:
        return None
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        found = find_component_by_class(child, class_name)
        if found is not None:
            return found
    return None


def find_components_by_id_type(component, component_type):
    matches = []
    component_id = getattr(component, "id", None)
    if isinstance(component_id, dict) and component_id.get("type") == component_type:
        matches.append(component)
    children = getattr(component, "children", None)
    if children is None:
        return matches
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        matches.extend(find_components_by_id_type(child, component_type))
    return matches


class ResearchWebTests(unittest.TestCase):
    def setUp(self):
        self.results = (
            make_result(AlphaStrategy(), [100, 110, 121], [{
                "Date": "2024-01-02",
                "PreWeights": {"QQQ": 0.70, "BIL": 0.30},
                "Target": {"QQQ": 0.80, "BIL": 0.20},
                "ExecutionDays": 2,
            }]),
            make_result(BetaStrategy(), [100, 105, 106]),
        )
        self.view = ResearchViewModel(self.results)

    def test_browser_load_publishes_the_latest_strategy_snapshot(self):
        latest_results = (
            make_result(AlphaStrategy(), [100, 110, 121]),
            make_result(GammaStrategy(), [100, 103, 109]),
        )

        class ResultStore:
            def __init__(self):
                self.refresh_count = 0

            def snapshot(self):
                return StrategyResultSnapshot(latest_results, version=2)

            def refresh_if_changed(self):
                self.refresh_count += 1
                return self.snapshot()

        store = ResultStore()
        app = create_research_app(self.results, result_store=store)
        layout_response = app.server.test_client().get("/_dash-layout")
        self.assertEqual(layout_response.status_code, 200)
        self.assertIn(
            "no-store", layout_response.headers.get("Cache-Control", "")
        )
        layout_text = layout_response.get_data(as_text=True)
        self.assertIn("GammaStrategy", layout_text)
        reload_callback = next(
            metadata["callback"].__wrapped__
            for metadata in app.callback_map.values()
            if "research-location" in {
                item["id"] for item in metadata["inputs"]
            }
        )

        output = reload_callback(
            "/", 1, ["AlphaStrategy"], "AlphaStrategy", "", 1, [], []
        )

        self.assertGreaterEqual(store.refresh_count, 1)
        self.assertEqual(output[0], 2)
        self.assertEqual(output[3], "2개 전략")
        self.assertEqual(output[8], [
            {"label": "AlphaStrategy", "value": "AlphaStrategy"},
            {"label": "GammaStrategy", "value": "GammaStrategy"},
        ])
        self.assertEqual(output[9], ["AlphaStrategy"])
        self.assertEqual(output[11], "AlphaStrategy")
        self.assertEqual(output[12][1]["Strategy"], "GammaStrategy")

    def test_figures_project_existing_result_data(self):
        performance = self.view.performance_figure(["AlphaStrategy"])
        drawdown = self.view.drawdown_figure(["AlphaStrategy"])
        allocation = self.view.allocation_figure("AlphaStrategy")
        rebalances = self.view.rebalance_figure("AlphaStrategy")

        self.assertEqual([trace.name for trace in performance.data], ["AlphaStrategy"])
        self.assertEqual([round(value, 6) for value in performance.data[0].y], [0.0, 10.0, 21.0])
        self.assertEqual(min(drawdown.data[0].y), 0.0)
        self.assertEqual({trace.name for trace in allocation.data}, {"QQQ", "BIL"})
        self.assertEqual(rebalances.data[1].name, "리밸런싱")
        self.assertEqual(len(rebalances.data[1].x), 1)
        self.assertIn("70.00%", rebalances.data[1].text[0])
        self.assertIn("80.00%", rebalances.data[1].text[0])
        self.assertNotIn("<b>", rebalances.data[1].text[0])
        self.assertEqual(performance.layout.xaxis.hoverformat, "%Y.%m.%d")
        self.assertIsNone(performance.data[0].hovertemplate)
        self.assertEqual(performance.data[0].hoverinfo, "none")
        self.assertEqual(performance.layout.hoverdistance, -1)
        self.assertEqual(performance.layout.spikedistance, -1)
        for figure in (performance, drawdown):
            self.assertEqual(figure.layout.margin.t, 96)
            self.assertEqual(figure.layout.xaxis.rangeselector.x, 0)
            self.assertEqual(figure.layout.xaxis.rangeselector.xanchor, "left")
            self.assertAlmostEqual(figure.layout.xaxis.rangeselector.y, 1.13)
            self.assertEqual(figure.layout.legend.entrywidth, 0.5)
            self.assertEqual(figure.layout.legend.entrywidthmode, "fraction")
        self.assertEqual(performance.data[0].customdata[0], {
            "date": "2024.01.01", "name": "AlphaStrategy", "value": "0.00%",
        })
        self.assertEqual(performance.layout.hoverlabel.font.weight, "normal")
        self.assertIn("-apple-system", performance.layout.hoverlabel.font.family)
        self.assertIn("Apple SD Gothic Neo", performance.layout.hoverlabel.font.family)
        self.assertIn("Noto Sans KR", performance.layout.hoverlabel.font.family)
        self.assertNotIn("Mono", performance.layout.hoverlabel.font.family)
        self.assertEqual(performance.layout.hoverlabel.font.size, 12)
        self.assertEqual(
            performance.layout.hoverlabel.bgcolor,
            "rgba(255,255,255,0.96)",
        )

        self.assertEqual(performance.layout.hoverlabel.bordercolor, "#DCE1E7")
        self.assertEqual(performance.layout.hoverlabel.font.color, "#182433")
        self.assertNotIn("%{x", allocation.data[0].hovertemplate)
        self.assertNotIn("<b>", allocation.data[0].hovertemplate)

        comparison = self.view.performance_figure(
            ["AlphaStrategy", "BetaStrategy"]
        )
        self.assertTrue(all(trace.hoverinfo == "none" for trace in comparison.data))
        self.assertTrue(all(
            set(item) == {"date", "name", "value"}
            for trace in comparison.data for item in trace.customdata
        ))
        self.assertAlmostEqual(self.view.total_return("AlphaStrategy"), 0.21)
        self.assertAlmostEqual(
            self.view.total_return("AlphaStrategy", "2024-01-02", "2024-01-03"),
            0.1,
        )

    def test_comparison_selector_keeps_a_fixed_gap_above_legend(self):
        results = tuple(
            make_result(type(f"Strategy{index}", (), {})(), [100, 101, 102])
            for index in range(10)
        )
        figure = ResearchViewModel(results).performance_figure()
        legend_rows = 5
        plot_height = 450 - figure.layout.margin.t - figure.layout.margin.b
        selector_gap = (
            (figure.layout.xaxis.rangeselector.y - figure.layout.legend.y)
            * plot_height
            - 19 * legend_rows
        )

        self.assertEqual(figure.layout.margin.t, 147)
        self.assertAlmostEqual(selector_gap, 14)

    def test_allocation_chart_sends_only_weight_change_points(self):
        result = make_result(AlphaStrategy(), [100, 101, 102])
        result["history"]["Weights"] = [
            {"QQQ": 0.7, "BIL": 0.3},
            {"QQQ": 0.7, "BIL": 0.3},
            {"QQQ": 0.7, "BIL": 0.3},
        ]

        allocation = ResearchViewModel((result,)).allocation_figure("AlphaStrategy")

        self.assertEqual(len(allocation.data[0].x), 2)

    def test_detail_charts_use_horizontal_time_navigation(self):
        allocation = self.view.allocation_figure("AlphaStrategy")
        rebalances = self.view.rebalance_figure("AlphaStrategy")

        for figure in (allocation, rebalances):
            self.assertEqual(figure.layout.dragmode, "pan")
            self.assertTrue(figure.layout.yaxis.fixedrange)
            self.assertEqual(figure.layout.xaxis.tickformat, "%Y.%m")
            self.assertEqual(
                [stop.value for stop in figure.layout.xaxis.tickformatstops],
                ["%Y.%m.%d", "%Y.%m"],
            )
            self.assertEqual(
                pd.Timestamp(figure.layout.xaxis.minallowed),
                pd.Timestamp("2024-01-01"),
            )
            self.assertEqual(
                pd.Timestamp(figure.layout.xaxis.maxallowed),
                pd.Timestamp("2024-01-03"),
            )
            self.assertEqual(
                tuple(figure.layout.xaxis.rangeslider.range),
                ("2024-01-01T00:00:00", "2024-01-03T00:00:00"),
            )
        self.assertFalse(allocation.layout.xaxis.rangeslider.visible)
        self.assertEqual(len(allocation.layout.annotations), 0)
        self.assertEqual(len(allocation.layout.xaxis.rangeselector.buttons), 0)
        self.assertTrue(rebalances.layout.xaxis.rangeslider.visible)
        self.assertEqual(rebalances.layout.margin.t, 96)
        self.assertEqual(
            rebalances.layout.xaxis.rangeselector.yanchor,
            "bottom",
        )
        self.assertEqual(
            [annotation.text for annotation in rebalances.layout.annotations],
            ["2024"],
        )
        self.assertEqual(
            [button.label for button in rebalances.layout.xaxis.rangeselector.buttons],
            ["1년", "3년", "5년", "전체"],
        )

        combined = self.view.combined_detail_figure("AlphaStrategy")
        self.assertEqual({trace.type for trace in combined.data}, {"scatter"})
        self.assertEqual(
            [trace.stackgroup for trace in combined.data[:2]],
            ["portfolio-return", "portfolio-return"],
        )
        self.assertTrue(combined.layout.xaxis.rangeslider.visible)
        self.assertEqual(
            [round(sum(values), 6) for values in zip(
                combined.data[0].y, combined.data[1].y
            )],
            [0.0, 10.0, 21.0],
        )
        self.assertEqual(
            [round(value, 6) for value in combined.data[2].y],
            [0.0, 10.0, 21.0],
        )
        self.assertEqual(
            combined.layout.yaxis.title.text,
            "누적 수익률 (%)",
        )
        self.assertEqual(combined.data[0].hoverinfo, "skip")
        self.assertEqual(combined.data[1].hoverinfo, "skip")
        self.assertEqual(combined.data[3].hoverinfo, "skip")
        regular_hover = combined.data[2].customdata[0]
        rebalance_hover = combined.data[2].customdata[1]
        self.assertEqual(regular_hover["date"], "2024.01.01")
        self.assertEqual(regular_hover["return"], "0.00%")
        self.assertEqual(
            regular_hover["assets"],
            [
                {"name": "QQQ", "current": "70.00%", "target": None},
                {"name": "BIL", "current": "30.00%", "target": None},
            ],
        )
        self.assertEqual(rebalance_hover["assets"][0]["target"], "80.00%")
        self.assertEqual(rebalance_hover["assets"][1]["target"], "20.00%")
        self.assertEqual(rebalance_hover["assets"][0]["current"], "70.00%")
        self.assertEqual(rebalance_hover["assets"][1]["current"], "30.00%")
        self.assertEqual(rebalance_hover["executionDays"], 2)
        self.assertEqual(combined.data[2].hoverinfo, "none")
        self.assertIsNone(combined.data[2].hovertemplate)
        self.assertEqual(combined.layout.xaxis.hoverformat, "%Y.%m.%d")
        self.assertFalse(combined.data[2].showlegend)
        self.assertEqual(combined.layout.hovermode, "x unified")
        self.assertEqual(
            combined.layout.xaxis.unifiedhovertitle.text,
            "%{x|%Y.%m.%d}",
        )
        combined_y_range = _visible_rebalance_y_range(
            combined.to_plotly_json(), {"xaxis.autorange": True}
        )
        self.assertEqual(combined_y_range[0], 0.0)
        self.assertGreater(combined_y_range[1], 21.0)

        loss_result = make_result(AlphaStrategy(), [100, 90, 95])
        loss_figure = ResearchViewModel((loss_result,)).combined_detail_figure(
            "AlphaStrategy"
        )
        loss_y_range = _visible_rebalance_y_range(
            loss_figure.to_plotly_json(), {"xaxis.autorange": True}
        )
        self.assertLess(loss_y_range[0], -10.0)
        self.assertEqual(loss_y_range[1], 0.0)

        app = create_research_app(self.results)
        detail_graph = find_component(app.layout, "research-detail-graph")
        detail_tooltip = find_component(app.layout, "research-detail-tooltip")
        self.assertEqual(detail_graph.config["doubleClick"], "reset")
        self.assertTrue(detail_graph.clear_on_unhover)
        self.assertEqual(detail_tooltip.direction, "right")
        self.assertIn("zoom2d", detail_graph.config["modeBarButtonsToRemove"])
        self.assertIn("autoScale2d", detail_graph.config["modeBarButtonsToRemove"])

    def test_detail_chart_cursor_matches_pan_and_range_actions(self):
        stylesheet = (
            Path(__file__).parents[1] / "src" / "assets" / "research_web.css"
        ).read_text(encoding="utf-8")

        self.assertIn(".research-detail-graph .nsewdrag", stylesheet)
        self.assertIn(".research-detail-graph .rangeslider-slidebox", stylesheet)
        self.assertIn(".research-graph .nsewdrag {", stylesheet)
        self.assertIn('stroke=\'%23182433\'', stylesheet)
        self.assertIn('10 10, crosshair !important', stylesheet)
        self.assertIn("cursor: grab !important", stylesheet)
        self.assertIn("cursor: grabbing !important", stylesheet)
        self.assertIn("cursor: ew-resize !important", stylesheet)
        self.assertIn(") .dragcover", stylesheet)
        self.assertIn("Apple SD Gothic Neo", stylesheet)
        self.assertIn("grid-template-columns: max-content 60px", stylesheet)
        self.assertIn("grid-template-columns: max-content 60px 14px 60px", stylesheet)
        self.assertIn("padding: 6px 8px", stylesheet)
        self.assertIn(".research-custom-tooltip-separator", stylesheet)
        self.assertIn("border-bottom: 1px solid #e6e9ee", stylesheet)
        self.assertIn(".research-custom-tooltip-row {\n  display: contents", stylesheet)
        self.assertIn("width: max-content", stylesheet)
        self.assertIn(".research-detail-card { overflow: visible; }", stylesheet)
        self.assertIn(
            ".research-card:has(.research-navigation-graph:hover)",
            stylesheet,
        )
        self.assertIn("z-index: 1200", stylesheet)
        self.assertIn("font-variant-numeric: tabular-nums lining-nums", stylesheet)

    def test_indicator_matrix_uses_internal_scrolling_without_overlap(self):
        stylesheet = (
            Path(__file__).parents[1] / "src" / "assets" / "research_web.css"
        ).read_text(encoding="utf-8")

        self.assertIn(".research-indicator-matrix-scroll", stylesheet)
        self.assertIn("overflow-x: auto", stylesheet)
        self.assertIn("width: max(100%", stylesheet)
        self.assertIn("text-overflow: ellipsis", stylesheet)
        self.assertIn("position: sticky", stylesheet)
        self.assertIn(
            ".research-indicator-toolbar { grid-template-columns: 1fr; }",
            stylesheet,
        )
        self.assertIn(".research-indicator-controls {", stylesheet)
        self.assertIn("z-index: 5", stylesheet)
        self.assertIn("overflow: visible", stylesheet)
        self.assertIn(".research-indicator-editor[open]", stylesheet)
        self.assertIn("flex-wrap: nowrap", stylesheet)
        self.assertIn("min-height: 38px", stylesheet)
        self.assertIn("top: 50%", stylesheet)
        self.assertIn("left: 50%", stylesheet)
        self.assertIn("transform: translate(-50%, -50%)", stylesheet)
        self.assertNotIn(
            ".research-indicator-matrix-checklist label:has(input:checked) {",
            stylesheet,
        )

    def test_rebalance_y_range_follows_the_visible_time_window(self):
        figure = self.view.rebalance_figure("AlphaStrategy").to_plotly_json()

        full_range = _visible_rebalance_y_range(
            figure, {"xaxis.autorange": True}
        )
        narrow_range = _visible_rebalance_y_range(figure, {
            "xaxis.range[0]": "2024-01-02",
            "xaxis.range[1]": "2024-01-03",
        })

        self.assertIsNotNone(full_range)
        self.assertIsNotNone(narrow_range)
        self.assertLess(narrow_range[1] - narrow_range[0], full_range[1] - full_range[0])
        self.assertLess(narrow_range[0], 10.0)
        self.assertGreater(narrow_range[1], 21.0)

    def test_allocation_downsampling_preserves_material_jumps(self):
        index = pd.date_range("2020-01-01", periods=1000, freq="D")
        qqq = [0.5 + day * 0.00001 + (0.1 if day >= 500 else 0.0) for day in range(1000)]
        history = pd.DataFrame({
            "Weights": [
                {"QQQ": weight, "BIL": 1.0 - weight}
                for weight in qqq
            ],
        }, index=index)

        compact = _weight_change_frame(history, max_points=100)

        self.assertLess(len(compact), 130)
        self.assertIn(index[499], compact.index)
        self.assertIn(index[500], compact.index)

    def test_mapped_product_hides_qqq_and_identifies_its_family(self):
        result = make_result(KodexNasdaqAllocationStrategy(), [100, 101, 102])
        result["history"]["Weights"] = [
            {"QQQ": 0.0, "379810.KS": 0.7, "BND": 0.3},
            {"QQQ": 0.0, "379810.KS": 0.71, "BND": 0.29},
            {"QQQ": 0.0, "379810.KS": 0.69, "BND": 0.31},
        ]

        allocation = ResearchViewModel((result,)).allocation_figure(
            "KodexNasdaqAllocationStrategy"
        )
        names = {trace.name for trace in allocation.data}

        self.assertNotIn("QQQ", names)
        self.assertIn("KODEX 미국나스닥100 · QQQ 계열", names)
        self.assertIn("BND", names)

    def test_indicator_research_builds_synchronized_selected_panels(self):
        figure = self.view.indicator_figure(
            ["QQQ"], ["Close", "RSI14", "MDD252"],
            "2024-01-01", "2024-01-03",
        )

        self.assertEqual(
            [trace.name for trace in figure.data],
            ["QQQ · 종가", "QQQ · RSI14", "QQQ · MDD 252일"],
        )
        self.assertEqual(
            [round(value, 6) for value in figure.data[0].y],
            [100.0, 102.0, 101.0],
        )
        self.assertEqual(figure.data[0].hoverinfo, "none")
        self.assertIsNone(figure.data[0].customdata)
        self.assertEqual(figure.data[0].meta["tooltipName"], "QQQ · 종가")
        self.assertEqual(figure.data[0].meta["panel"], "price")
        self.assertEqual(
            [annotation.text for annotation in figure.layout.annotations],
            ["가격·추세", "오실레이터", "리스크", "2024"],
        )
        self.assertTrue(all(
            annotation.x == 0 and annotation.xanchor == "left"
            for annotation in figure.layout.annotations[:3]
        ))
        self.assertEqual(figure.layout.height, 972)
        self.assertEqual(figure.layout.yaxis.title.text, "기준=100")
        self.assertEqual(figure.layout.dragmode, "pan")
        self.assertEqual(figure.layout.xaxis3.tickformat, "%Y.%m")
        self.assertEqual(
            [stop.value for stop in figure.layout.xaxis3.tickformatstops],
            ["%Y.%m.%d", "%Y.%m"],
        )
        self.assertTrue(figure.layout.xaxis3.rangeslider.visible)
        plot_height = (
            figure.layout.height
            - figure.layout.margin.t
            - figure.layout.margin.b
        )
        self.assertAlmostEqual(
            figure.layout.xaxis3.rangeslider.thickness * plot_height,
            0.09 * (520 - 96 - 78),
        )
        self.assertEqual(
            figure.layout.xaxis3.rangeslider.bgcolor,
            "rgba(106,109,120,0.06)",
        )
        self.assertEqual(
            pd.Timestamp(figure.layout.xaxis3.minallowed),
            pd.Timestamp("2024-01-01"),
        )
        self.assertEqual(
            pd.Timestamp(figure.layout.xaxis3.maxallowed),
            pd.Timestamp("2024-01-03"),
        )
        self.assertEqual(
            [button.label for button in figure.layout.xaxis3.rangeselector.buttons],
            ["1년", "3년", "5년", "전체"],
        )
        self.assertAlmostEqual(
            figure.layout.annotations[-1].y * plot_height,
            -0.235 * (520 - 96 - 78),
        )
        self.assertEqual(figure.layout.margin.t, 117)
        self.assertEqual(figure.layout.xaxis3.rangeselector.x, 0)
        self.assertEqual(figure.layout.xaxis3.rangeselector.xanchor, "left")
        self.assertAlmostEqual((figure.layout.legend.y - 1) * plot_height, 42)
        self.assertAlmostEqual(
            (figure.layout.xaxis3.rangeselector.y - figure.layout.legend.y)
            * plot_height - 19,
            24,
        )
        self.assertEqual(figure.layout.legend.entrywidth, 0.2)
        self.assertEqual(figure.layout.legend.entrywidthmode, "fraction")

    def test_indicator_research_draws_only_explicit_ticker_indicator_pairs(self):
        result = make_result(AlphaStrategy(), [100, 110, 121])
        result["market_data"]["BND"] = result["market_data"]["QQQ"].copy()

        view = ResearchViewModel((result,))
        figure = view.indicator_figure(
            None,
            None,
            selected_pairs=[("QQQ", "Close"), ("BND", "RSI14")],
        )

        self.assertEqual(
            [trace.name for trace in figure.data],
            ["QQQ · 종가", "BND · RSI14"],
        )

    def test_indicator_reuses_market_frames_and_strategy_overlay_cache(self):
        result = make_result(AlphaStrategy(), [100, 110, 121])
        view = ResearchViewModel((result,))

        self.assertIs(view.market_frames, view.market_frames)
        self.assertIs(view.market_frames["QQQ"], result["market_data"]["QQQ"])
        view.indicator_figure(
            ["QQQ"], ["Close"], overlay_strategy="AlphaStrategy",
        )
        cached = view._indicator_overlay_cache[
            ("AlphaStrategy", "", "")
        ]
        view.indicator_figure(
            ["QQQ"], ["RSI14"], overlay_strategy="AlphaStrategy",
        )

        self.assertEqual(len(view._indicator_overlay_cache), 1)
        self.assertIs(
            view._indicator_overlay_cache[("AlphaStrategy", "", "")],
            cached,
        )

    def test_indicator_matrix_row_and_column_bulk_toggles_are_scoped(self):
        row_ids = [
            {"type": "indicator-matrix-row", "column": "Close"},
            {"type": "indicator-matrix-row", "column": "EMA55"},
            {"type": "indicator-matrix-row", "column": "RSI14"},
        ]
        tickers = ["QQQ", "BND"]

        row_selected = _toggle_indicator_matrix_values(
            {"type": "indicator-row-toggle", "column": "EMA55"},
            [["QQQ"], [], []], row_ids, tickers,
        )
        self.assertEqual(row_selected, [["QQQ"], ["QQQ", "BND"], []])

        column_selected = _toggle_indicator_matrix_values(
            {"type": "indicator-column-toggle", "panel": "price", "ticker": "BND"},
            [["QQQ"], [], ["QQQ"]], row_ids, tickers,
        )
        self.assertEqual(column_selected, [["QQQ", "BND"], ["BND"], ["QQQ"]])

    def test_indicator_research_can_overlay_strategy_states_and_rebalances(self):
        result = make_result(AlphaStrategy(), [100, 110, 121], [{
            "Date": "2024-01-02",
            "ExecutionDate": "2024-01-02",
            "Target": {"QQQ": 0.8, "BIL": 0.2},
        }])
        result["history"]["StrategyState"] = ["BULL", "BEAR", "BEAR"]
        view = ResearchViewModel((result,))
        figure = view.indicator_figure(
            ["QQQ"], ["Close"], overlay_strategy="AlphaStrategy",
            overlay_options=["states", "rebalances"],
        )

        self.assertGreaterEqual(len(figure.layout.shapes), 2)
        self.assertEqual(figure.data[-1].name, "AlphaStrategy · 리밸런싱")
        self.assertFalse(figure.data[-1].showlegend)
        self.assertEqual(figure.data[-2].name, "AlphaStrategy")
        self.assertEqual(
            [round(value, 6) for value in figure.data[-2].y],
            [100.0, 110.0, 121.0],
        )
        self.assertEqual(
            [round(value, 6) for value in figure.data[-1].y],
            [110.0],
        )
        self.assertIsNone(figure.data[0].customdata)
        self.assertTrue(figure.data[-2].meta["isStrategySeries"])
        tooltip_data = view.indicator_tooltip_data("AlphaStrategy")
        regular_portfolio = tooltip_data["2024-01-01"]["portfolio"]
        rebalance_portfolio = tooltip_data["2024-01-02"]["portfolio"]
        self.assertEqual(regular_portfolio["return"], "0.00%")
        self.assertEqual(
            regular_portfolio["assets"],
            [
                {"name": "QQQ", "current": "70.00%", "target": None},
                {"name": "BIL", "current": "30.00%", "target": None},
            ],
        )
        self.assertEqual(rebalance_portfolio["return"], "10.00%")
        self.assertEqual(rebalance_portfolio["assets"][0]["target"], "80.00%")
        self.assertEqual(rebalance_portfolio["assets"][1]["target"], "20.00%")
        multi_panel = ResearchViewModel((result,)).indicator_figure(
            ["QQQ"], ["Close", "RSI14"], overlay_strategy="AlphaStrategy",
            overlay_options=["states", "rebalances"],
        )
        oscillator_trace = next(
            trace for trace in multi_panel.data if trace.name == "QQQ · RSI14"
        )
        self.assertIsNone(oscillator_trace.customdata)
        self.assertEqual(oscillator_trace.meta["panel"], "oscillator")
        self.assertEqual(tooltip_data["2024-01-01"]["state"], "상승")
        self.assertEqual(tooltip_data["2024-01-02"]["state"], "하락")
        self.assertIn(".18)", figure.layout.shapes[0].fillcolor)
        self.assertGreater(figure.layout.shapes[0].line.width, 0)
        self.assertEqual(figure.layout.shapes[0].xref, "x")
        self.assertEqual(figure.layout.shapes[0].yref, "y domain")

        hidden = ResearchViewModel((result,)).indicator_figure(
            ["QQQ"], ["Close"], overlay_strategy="",
            overlay_options=["states", "rebalances"],
        )
        self.assertEqual(len(hidden.layout.shapes), 0)
        self.assertNotIn(
            "AlphaStrategy · 리밸런싱",
            [trace.name for trace in hidden.data],
        )

        oscillator_only = ResearchViewModel((result,)).indicator_figure(
            ["QQQ"], ["RSI14"], overlay_strategy="AlphaStrategy",
            overlay_options=["rebalances"],
        )
        self.assertEqual(
            [annotation.text for annotation in oscillator_only.layout.annotations[:2]],
            ["가격·추세", "오실레이터"],
        )
        self.assertIn(
            "AlphaStrategy",
            [trace.name for trace in oscillator_only.data],
        )

    def test_indicator_zoom_rescales_each_visible_y_axis(self):
        result = make_result(AlphaStrategy(), [100, 110, 121])
        result["market_data"]["QQQ"]["Close"] = [100.0, 102.0, 240.0]
        result["market_data"]["QQQ"]["RSI14"] = [50.0, 58.0, 92.0]
        figure = ResearchViewModel((result,)).indicator_figure(
            ["QQQ"], ["Close", "RSI14"]
        )

        ranges = _visible_indicator_y_ranges(
            figure.to_plotly_json(), ["2024-01-01", "2024-01-02"]
        )

        self.assertEqual(set(ranges), {"yaxis", "yaxis2"})
        self.assertLess(ranges["yaxis"][1], 120.0)
        self.assertLess(ranges["yaxis2"][1], 70.0)

    def test_summary_columns_use_compact_display_formats(self):
        app = create_research_app(self.results)
        grid = find_component(app.layout, "research-summary-grid")
        columns = {column["field"]: column for column in grid.columnDefs}

        self.assertIn("d3.format('.2%')", columns["CAGR"]["valueFormatter"]["function"])
        self.assertIn("d3.format('.2f')", columns["Sharpe"]["valueFormatter"]["function"])
        self.assertIn("d3.format('.2%')", columns["TotalReturn"]["valueFormatter"]["function"])
        self.assertIn("d3.format(',.4f')", columns["TransactionCosts"]["valueFormatter"]["function"])
        self.assertNotIn("Start", columns)
        self.assertNotIn("End", columns)
        self.assertIn("replaceAll('-', '.')", columns["StartDate"]["valueFormatter"]["function"])
        self.assertIn("replaceAll('-', '.')", columns["EndDate"]["valueFormatter"]["function"])
        self.assertAlmostEqual(grid.rowData[0]["TotalReturn"], 0.21)
        self.assertEqual(grid.rowData[0]["StartDate"], "2024-01-01")
        self.assertEqual(grid.rowData[0]["EndDate"], "2024-01-03")
        self.assertTrue(columns["Strategy"]["suppressSizeToFit"])
        self.assertEqual(columns["CAGR"]["minWidth"], 78)
        self.assertEqual(columns["TransactionCosts"]["minWidth"], 96)
        self.assertEqual(columns["EndDate"]["minWidth"], 104)
        self.assertEqual(grid.columnSizeOptions["defaultMinWidth"], 72)
        self.assertEqual(grid.dashGridOptions["rowHeight"], 38)
        self.assertEqual(grid.columnSize, "responsiveSizeToFit")

        LongStrategy = type(
            "RetirementAllocationStrategyWithAnEspeciallyLongDisplayName",
            (),
            {},
        )
        long_app = create_research_app((
            make_result(LongStrategy(), [100, 110, 121]),
        ))
        long_grid = find_component(long_app.layout, "research-summary-grid")
        long_strategy_column = next(
            column for column in long_grid.columnDefs
            if column["field"] == "Strategy"
        )
        self.assertGreater(long_strategy_column["width"], 210)
        self.assertEqual(
            long_strategy_column["width"],
            long_strategy_column["minWidth"],
        )
        self.assertIsNone(find_component_by_class(
            app.layout, "research-card-icon research-summary-icon"
        ))

    def test_strategy_outside_selected_period_is_not_added_to_comparison(self):
        performance = self.view.performance_figure(
            ["AlphaStrategy"], "2023-01-01", "2023-12-31"
        )
        drawdown = self.view.drawdown_figure(
            ["AlphaStrategy"], "2023-01-01", "2023-12-31"
        )

        self.assertEqual(len(performance.data), 0)
        self.assertEqual(len(drawdown.data), 0)

    def test_app_contains_result_views_and_callback(self):
        app = create_research_app(self.results)

        self.assertEqual(app.title, "Investment Strategy Research")
        self.assertEqual(len(app.callback_map), 19)
        strategy_selector = find_component(app.layout, "research-strategies")
        self.assertTrue(strategy_selector.persistence)
        self.assertEqual(strategy_selector.persistence_type, "local")
        self.assertIsNotNone(find_component(app.layout, "research-kpi-cagr"))
        self.assertEqual(
            find_component(app.layout, "research-kpi-total-return").children,
            "21.00%",
        )
        self.assertIsNone(find_component(app.layout, "research-kpi-end"))
        self.assertIsNotNone(find_component(app.layout, "research-theme-toggle"))
        self.assertIsNotNone(find_component(app.layout, "research-performance"))
        self.assertIsNotNone(find_component(app.layout, "research-drawdown"))
        self.assertIsNotNone(find_component(app.layout, "research-detail-graph"))
        self.assertIsNotNone(find_component(app.layout, "research-view-mode"))
        self.assertIsNotNone(find_component(app.layout, "research-analysis-view"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-view"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-graph"))
        self.assertTrue(
            find_component(app.layout, "research-indicator-graph").clear_on_unhover
        )
        self.assertIsNotNone(find_component(app.layout, "research-indicator-tooltip"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-panel"))
        indicator_overlays = find_component(
            app.layout, "research-indicator-overlays"
        )
        self.assertEqual(indicator_overlays.style, {"display": "none"})
        self.assertIsNotNone(find_component(
            app.layout, "research-indicator-overlay-hint"
        ))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-selection-summary"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-matrix-price"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-matrix-oscillator"))
        self.assertIsNotNone(find_component(app.layout, "research-indicator-matrix-risk"))
        self.assertIsNone(find_component(app.layout, "research-indicator-tickers"))
        indicator_editor = find_component_by_class(
            app.layout, "research-indicator-editor"
        )
        self.assertIsNotNone(indicator_editor)
        self.assertFalse(indicator_editor.open)
        self.assertIsNotNone(find_component_by_class(
            app.layout, "research-indicator-editor-summary"
        ))
        matrix_rows = find_components_by_id_type(
            app.layout, "indicator-matrix-row"
        )
        self.assertEqual(len(matrix_rows), 8)
        self.assertTrue(all(row.persistence for row in matrix_rows))
        self.assertTrue(find_component(app.layout, "research-view-mode").persistence)
        self.assertIsNotNone(find_component(app.layout, "research-detail-tooltip"))
        self.assertIsNotNone(find_component(app.layout, "research-performance-tooltip"))
        self.assertIsNotNone(find_component(app.layout, "research-drawdown-tooltip"))
        self.assertIn(
            "research-detail-tooltip",
            find_component(app.layout, "research-detail-tooltip").className,
        )
        self.assertIn(
            "research-comparison-tooltip",
            find_component(app.layout, "research-performance-tooltip").className,
        )
        self.assertIsNone(find_component(app.layout, "research-allocation-graph"))
        self.assertIsNone(find_component(app.layout, "research-rebalance-graph"))
        self.assertIsNone(find_component(app.layout, "research-detail-tabs"))
        self.assertIsNone(find_component_by_class(
            app.layout, "research-detail-chart-title"
        ))

        indicator_callback = next(
            metadata for key, metadata in app.callback_map.items()
            if "research-indicator-graph.figure" in key
        )
        indicator_inputs = {item["id"] for item in indicator_callback["inputs"]}
        self.assertEqual(indicator_inputs, {
            '{"column":["ALL"],"type":"indicator-matrix-row"}',
            "research-indicator-date-range",
            "research-indicator-strategy",
            "research-indicator-overlays",
            "research-result-version",
        })
        reload_callback = next(
            metadata for metadata in app.callback_map.values()
            if "research-location" in {
                item["id"] for item in metadata["inputs"]
            }
        )
        self.assertIn(
            "research-reload-trigger",
            {item["id"] for item in reload_callback["inputs"]},
        )

    def test_graphs_are_present_initially_and_detail_updates_are_isolated(self):
        app = create_research_app(self.results)

        self.assertEqual(len(find_component(app.layout, "research-performance").figure.data), 2)
        self.assertEqual(len(find_component(app.layout, "research-drawdown").figure.data), 2)
        self.assertEqual(
            find_component(app.layout, "research-performance").style["height"],
            "450px",
        )
        self.assertEqual(
            find_component(app.layout, "research-drawdown").style["height"],
            "450px",
        )
        self.assertTrue(find_component(app.layout, "research-performance").clear_on_unhover)
        self.assertTrue(find_component(app.layout, "research-drawdown").clear_on_unhover)
        for graph_id in ("research-performance", "research-drawdown"):
            graph = find_component(app.layout, graph_id)
            self.assertEqual(graph.figure.layout.dragmode, "pan")
            self.assertEqual(graph.figure.layout.margin.t, 96)
            self.assertEqual(graph.figure.layout.xaxis.rangeselector.x, 0)
            self.assertEqual(graph.figure.layout.xaxis.rangeselector.xanchor, "left")
            self.assertAlmostEqual(graph.figure.layout.xaxis.rangeselector.y, 1.13)
            self.assertEqual(
                graph.figure.layout.xaxis.rangeselector.yanchor,
                "bottom",
            )
            self.assertFalse(graph.figure.layout.xaxis.rangeslider.visible)
            self.assertEqual(
                [button.label for button in graph.figure.layout.xaxis.rangeselector.buttons],
                ["1년", "3년", "5년", "전체"],
            )
            self.assertEqual(
                pd.Timestamp(graph.figure.layout.xaxis.minallowed),
                pd.Timestamp("2024-01-01"),
            )
            self.assertEqual(
                pd.Timestamp(graph.figure.layout.xaxis.maxallowed),
                pd.Timestamp("2024-01-03"),
            )
            self.assertIn("zoom2d", graph.config["modeBarButtonsToRemove"])
        source = (
            Path(__file__).parents[1] / "src" / "research_web.py"
        ).read_text(encoding="utf-8")
        self.assertIn("(traceFor(point).meta || {}).isStrategySeries", source)
        self.assertIn(
            'research-indicator-tooltip-section-separator',
            source,
        )
        self.assertIn("strategyContext.portfolio", source)
        self.assertIn('row(returnLabel, portfolio.return', source)
        self.assertIn('`누적 수익률 (${strategyContext.state})`', source)
        self.assertIn('graphId === "research-performance"', source)
        self.assertIn('graphId === "research-drawdown"', source)
        self.assertIn('direction = spaceAbove >= spaceBelow ? "top" : "bottom"', source)
        bounded_navigation_callbacks = [
            metadata for metadata in app.callback_map.values()
            if any(
                item["property"] == "relayoutData"
                and item["id"] in {
                    "research-performance", "research-drawdown",
                    "research-indicator-graph",
                }
                for item in metadata["inputs"]
            )
        ]
        self.assertEqual(len(bounded_navigation_callbacks), 3)
        detail_graph = find_component(app.layout, "research-detail-graph")
        self.assertEqual(len(detail_graph.figure.data), 4)
        self.assertEqual(detail_graph.style["height"], "520px")

        performance_inputs = {
            item["id"] for item in app.callback_map["research-performance.figure"]["inputs"]
        }
        detail_callback = next(
            meta for meta in app.callback_map.values()
            if "research-detail-strategy" in {item["id"] for item in meta["inputs"]}
        )
        detail_inputs = {item["id"] for item in detail_callback["inputs"]}
        self.assertNotIn("research-detail-strategy", performance_inputs)
        self.assertNotIn("research-strategies", detail_inputs)
        synchronization_callback = next(
            meta for meta in app.callback_map.values()
            if {"research-detail-graph"}
            == {item["id"] for item in meta["inputs"]}
        )
        self.assertEqual(
            {item["id"] for item in synchronization_callback["inputs"]},
            {"research-detail-graph"},
        )

        update_detail = detail_callback["callback"].__wrapped__
        detail, _, _, _, total_return = update_detail(
            "BetaStrategy", "2024-01-01", "2024-01-03", 1, None
        )
        self.assertEqual(
            detail.layout.datarevision,
            "BetaStrategy:2024-01-01:2024-01-03:1",
        )
        self.assertEqual({trace.name for trace in detail.data[:2]}, {"QQQ", "BIL"})
        self.assertEqual(detail.data[2].name, "누적 수익률")
        self.assertEqual(detail.data[3].name, "리밸런싱")
        self.assertEqual(total_return, "6.00%")

        shared_range = {"range": ["2024-01-02", "2024-01-03"]}
        detail, *_ = update_detail(
            "BetaStrategy", "2024-01-01", "2024-01-03", 1, shared_range
        )
        self.assertEqual(
            tuple(detail.layout.xaxis.range),
            ("2024-01-02T00:00:00", "2024-01-03T00:00:00"),
        )
        self.assertFalse(detail.layout.xaxis.autorange)

    def test_detail_range_state_is_reused_and_clamped(self):
        state = _detail_range_state({
            "xaxis.range[0]": "2023-12-01",
            "xaxis.range[1]": "2024-01-02",
        })
        figure = self.view.allocation_figure("AlphaStrategy")

        _apply_stored_detail_range(figure, state)

        self.assertEqual(
            tuple(figure.layout.xaxis.range),
            ("2024-01-01T00:00:00", "2024-01-03T00:00:00"),
        )
        _apply_stored_detail_range(
            figure, {"range": ["2024-01-03", "2024-01-04"]}
        )
        preserved_range = [pd.Timestamp(value) for value in figure.layout.xaxis.range]
        self.assertEqual(preserved_range[0], pd.Timestamp("2024-01-02"))
        self.assertEqual(preserved_range[1], pd.Timestamp("2024-01-03"))
        self.assertEqual(preserved_range[1] - preserved_range[0], pd.Timedelta(days=1))
        self.assertEqual(
            _detail_range_state({"xaxis.autorange": True}),
            {"autorange": True},
        )
        self.assertEqual(
            _detail_range_state({
                "xaxis3.range[0]": "2024-01-01",
                "xaxis3.range[1]": "2024-01-02",
            }),
            {"range": ["2024-01-01", "2024-01-02"]},
        )

    def test_long_detail_range_uses_denser_year_marks(self):
        result = make_result(AlphaStrategy(), [100, 110, 121])
        result["history"].index = pd.to_datetime([
            "2000-01-01", "2010-01-01", "2020-01-01"
        ])

        figure = ResearchViewModel((result,)).rebalance_figure("AlphaStrategy")
        year_labels = [annotation.text for annotation in figure.layout.annotations]

        self.assertGreaterEqual(len(year_labels), 10)
        self.assertEqual(year_labels[0], "2000")
        self.assertEqual(year_labels[-1], "2020")

    def test_comparison_graphs_rerender_for_every_selection_change(self):
        app = create_research_app(self.results)
        update_performance = app.callback_map[
            "research-performance.figure"
        ]["callback"].__wrapped__
        update_drawdown = app.callback_map[
            "research-drawdown.figure"
        ]["callback"].__wrapped__

        alpha_performance = update_performance(
            ["AlphaStrategy"], "2024-01-01", "2024-01-03", 1
        )
        beta_performance = update_performance(
            ["BetaStrategy"], "2024-01-01", "2024-01-03", 1
        )
        alpha_drawdown = update_drawdown(
            ["AlphaStrategy"], "2024-01-01", "2024-01-03", 1
        )
        beta_drawdown = update_drawdown(
            ["BetaStrategy"], "2024-01-01", "2024-01-03", 1
        )

        self.assertEqual([trace.name for trace in alpha_performance.data], ["AlphaStrategy"])
        self.assertEqual([trace.name for trace in beta_performance.data], ["BetaStrategy"])
        self.assertEqual([trace.name for trace in alpha_drawdown.data], ["AlphaStrategy"])
        self.assertEqual([trace.name for trace in beta_drawdown.data], ["BetaStrategy"])
        self.assertNotEqual(
            alpha_performance.layout.datarevision,
            beta_performance.layout.datarevision,
        )
        self.assertNotEqual(
            alpha_drawdown.layout.datarevision,
            beta_drawdown.layout.datarevision,
        )
        self.assertEqual(
            len(update_performance([], "2024-01-01", "2024-01-03", 1).data),
            0,
        )
        self.assertEqual(
            len(update_drawdown([], "2024-01-01", "2024-01-03", 1).data),
            0,
        )

    def test_detail_metrics_are_grouped_with_the_detail_strategy(self):
        app = create_research_app(self.results)
        detail_card = find_component_by_class(
            app.layout, "card research-card research-detail-card"
        )
        comparison_charts = find_component_by_class(
            app.layout, "research-chart-grid"
        )

        self.assertIsNotNone(find_component(detail_card, "research-kpi-cagr"))
        self.assertIsNone(find_component(comparison_charts, "research-kpi-cagr"))

    def test_main_defaults_to_existing_matplotlib_chart(self):
        self.assertEqual(parse_args([]).chart_backend, "matplotlib")
        self.assertEqual(parse_args(["--chart-backend", "dash", "--port", "9000"]).port, 9000)


if __name__ == "__main__":
    unittest.main()
