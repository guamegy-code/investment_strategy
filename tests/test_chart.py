import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chart import (
    build_selection,
    chart_tickers,
    chart_values_on_date,
    format_chart_value_popup,
    format_rebalance_table,
    fit_annotation_inside_axis,
    nearest_chart_date,
    rebalance_directions,
    rebalance_marker_events,
    set_date_guide,
    series_from_start,
    state_line_segments,
    strategy_risk_assets,
    strategy_state_series,
    ticker_chart_color,
)


class ChartTickerSelectionTests(unittest.TestCase):
    def test_new_selection_enables_qqq_disparity_by_default(self):
        class Strategy:
            pass

        market_data = {
            "QQQ": pd.DataFrame({"Close": [1.0]}),
            "BND": pd.DataFrame({"Close": [1.0]}),
        }

        selection = build_selection(
            [{"strategy": Strategy()}], market_data, saved={}
        )

        self.assertTrue(selection.matrix["Disparity"]["QQQ"])
        self.assertFalse(selection.matrix["Disparity"]["BND"])

    def test_ticker_label_and_graph_use_the_same_visible_order_color(self):
        visible_tickers = ("QQQ", "BND", "BIL", "VXUS")

        graph_color = ticker_chart_color("VXUS", visible_tickers, 10)
        checkbox_color = ticker_chart_color("VXUS", visible_tickers, 10)

        self.assertEqual(graph_color, checkbox_color)
        self.assertEqual(graph_color, "#FF9500")

    def test_active_alternative_risk_asset_is_added_to_chart_tickers(self):
        class VXUSStrategy:
            ALTERNATIVE_RISK_ASSET = "VXUS"

        tickers = chart_tickers([{"strategy": VXUSStrategy()}])

        self.assertIn("VXUS", tickers)
        self.assertEqual(len(tickers), len(set(tickers)))

    def test_new_ticker_is_visible_when_loading_an_older_saved_selection(self):
        class VXUSStrategy:
            pass

        saved = {
            "visible_tickers": ["QQQ"],
            "visible_rows": ["Price"],
            "matrix": {"Price": {"QQQ": True, "BND": False}},
        }
        market_data = {
            "QQQ": pd.DataFrame({"Close": [1.0]}),
            "BND": pd.DataFrame({"Close": [1.0]}),
            "VXUS": pd.DataFrame({"Close": [1.0]}),
        }

        selection = build_selection(
            [{"strategy": VXUSStrategy()}], market_data, saved
        )

        self.assertEqual(selection.visible_tickers, ["QQQ", "VXUS"])
        self.assertTrue(selection.matrix["Price"]["VXUS"])


class RebalanceMarkerTests(unittest.TestCase):
    def test_signal_is_marked_on_next_execution_date(self):
        dates = pd.bdate_range("2024-01-02", periods=4)
        history = pd.DataFrame({
            "Weights": [
                {"QQQ": 0.0, "BND": 0.0},
                {"QQQ": 0.7, "BND": 0.3},
                {"QQQ": 0.7, "BND": 0.3},
                {"QQQ": 0.4, "BND": 0.6},
            ]
        }, index=dates)
        trades = pd.DataFrame([
            {"Date": dates[1], "Ticker": "QQQ", "Shares": 1.0},
            {"Date": dates[1], "Ticker": "BND", "Shares": 0.5},
            {"Date": dates[3], "Ticker": "QQQ", "Shares": -0.4},
            {"Date": dates[3], "Ticker": "BND", "Shares": 0.4},
        ])
        rebalances = [
            {
                "Date": dates[0],
                "Target": {"QQQ": 0.7, "BND": 0.3},
                "ExecutionDays": 5,
            },
            {
                "Date": dates[2],
                "Target": {"QQQ": 0.4, "BND": 0.6},
                "ExecutionDays": 1,
            },
        ]

        directions = rebalance_directions(history, trades, rebalances)

        self.assertEqual(directions[dates[1]], "up")
        self.assertEqual(directions[dates[3]], "down")
        self.assertNotIn(dates[0], directions)
        self.assertNotIn(dates[2], directions)

        events = rebalance_marker_events(history, trades, rebalances)
        self.assertEqual(events[dates[1]]["target"], {"QQQ": 0.7, "BND": 0.3})
        self.assertEqual(events[dates[3]]["target"], {"QQQ": 0.4, "BND": 0.6})
        self.assertEqual(events[dates[1]]["execution_days"], 5)
        self.assertEqual(events[dates[3]]["execution_days"], 1)

    def test_direction_uses_total_risk_weight_and_marks_equal_as_same(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        risk_assets = ("LONG_KODEX_NASDAQ", "TIME", "KOACT")
        before = {
            "LONG_KODEX_NASDAQ": 0.35,
            "TIME": 0.21,
            "KOACT": 0.14,
            "BND": 0.30,
        }
        target = {
            "LONG_KODEX_NASDAQ": 0.28,
            "TIME": 0.28,
            "KOACT": 0.14,
            "BND": 0.30,
        }
        history = pd.DataFrame({"Weights": [before, target]}, index=dates)
        trades = pd.DataFrame([
            {
                "Date": dates[1],
                "Ticker": "LONG_KODEX_NASDAQ",
                "Shares": -1.0,
            },
            {"Date": dates[1], "Ticker": "TIME", "Shares": 1.0},
        ])
        rebalances = [{
            "Date": dates[0],
            "Target": target,
            "PreWeights": before,
            "ExecutionDays": 1,
        }]

        events = rebalance_marker_events(
            history, trades, rebalances, risk_assets=risk_assets
        )

        self.assertEqual(events[dates[1]]["direction"], "same")
        self.assertAlmostEqual(events[dates[1]]["before_risk"], 0.70)
        self.assertAlmostEqual(events[dates[1]]["target_risk"], 0.70)

    def test_rebalance_popup_aligns_before_and_target_percentages(self):
        before = {"QQQ": 0.684, "BND": 0.216, "BIL": 0.0, "GLD": 0.1}
        target = {"QQQ": 0.7, "BND": 0.2, "BIL": 0.0, "GLD": 0.1}

        table = format_rebalance_table(before, target, risk_assets=("QQQ",))

        self.assertIn("종목", table)
        self.assertIn("이전(%)", table)
        self.assertIn("목표(%)", table)
        rows = table.splitlines()[2:]
        self.assertEqual(rows[0].split()[0], "QQQ")
        first_decimal_columns = [row.index(".") for row in rows]
        self.assertEqual(len(set(first_decimal_columns)), 1)

    def test_long_risk_asset_names_stay_aligned_and_are_listed_first(self):
        before = {
            "BND": 0.30,
            "VERY_LONG_NASDAQ_PRODUCT": 0.40,
            "TIME": 0.30,
        }
        target = {
            "BND": 0.30,
            "VERY_LONG_NASDAQ_PRODUCT": 0.35,
            "TIME": 0.35,
        }

        table = format_rebalance_table(
            before,
            target,
            risk_assets=("VERY_LONG_NASDAQ_PRODUCT", "TIME"),
        )
        rows = table.splitlines()[2:]

        self.assertEqual(
            [row.split()[0] for row in rows],
            ["VERY_LONG_NASDAQ_PRODUCT", "TIME", "BND"],
        )
        first_numeric_columns = [row.index(row.split()[1]) for row in rows]
        second_numeric_columns = [row.rindex(row.split()[2]) for row in rows]
        self.assertEqual(len(set(first_numeric_columns)), 1)
        self.assertEqual(len(set(second_numeric_columns)), 1)

    def test_strategy_risk_assets_uses_multi_asset_sleeve(self):
        class MultiRiskStrategy:
            risk_assets = {"KODEX": 0.5, "TIME": 0.3, "KOACT": 0.2}

        self.assertEqual(
            strategy_risk_assets(MultiRiskStrategy()),
            ("KODEX", "TIME", "KOACT"),
        )

    def test_popup_is_clamped_inside_the_chart_axis(self):
        figure, axis = plt.subplots(figsize=(4, 2))
        annotation = axis.annotate(
            "체결일: 2024-01-03\n분할 체결 기간: 5거래일\n\n"
            "종목       이전(%)   목표(%)\nQQQ         68.4      70.0",
            xy=(0.99, 0.99),
            xycoords="axes fraction",
            xytext=(14, 18),
            textcoords="offset points",
            ha="left",
            va="bottom",
            multialignment="left",
        )
        figure.canvas.draw()

        fit_annotation_inside_axis(annotation, axis, figure)
        figure.canvas.draw()

        annotation_box = annotation.get_window_extent(figure.canvas.get_renderer())
        axis_box = axis.get_window_extent(figure.canvas.get_renderer())
        self.assertLessEqual(annotation_box.x1, axis_box.x1 - 7.5)
        self.assertLessEqual(annotation_box.y1, axis_box.y1 - 7.5)
        self.assertEqual(annotation._multialignment, "left")
        plt.close(figure)


class ChartValuePopupTests(unittest.TestCase):
    def test_vertical_date_guide_moves_to_popup_date_and_can_be_hidden(self):
        figure, axis = plt.subplots()
        guide = axis.axvline(0, visible=False)
        selected_date = pd.Timestamp("2024-01-08")

        set_date_guide(guide, selected_date)

        expected = mdates.date2num(selected_date)
        self.assertTrue(guide.get_visible())
        self.assertEqual(tuple(guide.get_xdata()), (expected, expected))

        set_date_guide(guide)

        self.assertFalse(guide.get_visible())
        plt.close(figure)

    def test_price_level_is_normalized_to_the_selected_start_date(self):
        dates = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.Series([100.0, 110.0, 121.0], index=dates)

        relative = series_from_start(
            prices, start_date=dates[1], normalize=True
        )

        self.assertEqual(relative.to_dict(), {dates[1]: 1.0, dates[2]: 1.1})

    def test_click_date_uses_nearest_visible_trading_date(self):
        dates = pd.to_datetime(["2024-01-05", "2024-01-08"])
        displayed = {
            "strategy:Dynamic": pd.Series([1.0, 1.1], index=dates),
            "price:QQQ": pd.Series([400.0, 405.0], index=dates),
        }

        selected = nearest_chart_date(pd.Timestamp("2024-01-07"), displayed)

        self.assertEqual(selected, pd.Timestamp("2024-01-08"))

    def test_values_use_one_selected_date_and_skip_missing_series(self):
        date = pd.Timestamp("2024-01-08")
        values = chart_values_on_date({
            "Dynamic": pd.Series(
                [1.0, 1.1],
                index=pd.to_datetime(["2024-01-05", "2024-01-08"]),
            ),
            "NotStarted": pd.Series(
                [1.0], index=pd.to_datetime(["2024-01-09"])
            ),
        }, date)

        self.assertEqual(values, {"Dynamic": 1.1})

    def test_popup_aligns_strategy_and_relative_price_levels(self):
        popup = format_chart_value_popup(
            pd.Timestamp("2024-01-08"),
            {"RetirementAllocationStrategy": 1.23456},
            {"379810.KS": 1.08765},
        )

        self.assertIn("날짜: 2024-01-08", popup)
        self.assertIn("전략", popup)
        self.assertIn("RetirementAllocationStrategy  1.2346", popup)
        self.assertIn("가격(시작일=1)", popup)
        self.assertIn("379810.KS", popup)
        self.assertIn("1.0877", popup)
        value_rows = [
            row for row in popup.splitlines()
            if row.startswith(("Retirement", "379810.KS"))
        ]
        number_columns = [row.index(row.split()[-1]) for row in value_rows]
        self.assertEqual(len(set(number_columns)), 1)


class StateColoredLineTests(unittest.TestCase):
    def test_each_line_segment_uses_its_starting_state(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        history = pd.DataFrame({
            "StrategyState": ["BULL", "CAUTION", "BEAR", "RECOVERY", "BULL"]
        }, index=dates)
        values = pd.Series([1.0, 1.1, 1.05, 1.03, 1.08], index=dates)

        segments, colors = state_line_segments(values, strategy_state_series(history))

        self.assertEqual(len(segments), 4)
        self.assertEqual(colors, ["#34C759", "#FFCC00", "#FF3B30", "#5AC8FA"])
        expected_points = np.column_stack((mdates.date2num(dates), values.to_numpy()))
        np.testing.assert_allclose(
            segments,
            np.stack((expected_points[:-1], expected_points[1:]), axis=1),
        )

    def test_missing_strategy_state_returns_empty_series(self):
        history = pd.DataFrame({"Portfolio": [1.0, 1.1]})

        self.assertTrue(strategy_state_series(history).empty)


if __name__ == "__main__":
    unittest.main()
