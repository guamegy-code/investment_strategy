import sys
import unittest
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chart import (
    format_rebalance_table,
    fit_annotation_inside_axis,
    rebalance_directions,
    rebalance_marker_events,
    state_line_segments,
    strategy_state_series,
)


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

    def test_rebalance_popup_aligns_before_and_target_percentages(self):
        before = {"QQQ": 0.684, "BND": 0.216, "BIL": 0.0, "GLD": 0.1}
        target = {"QQQ": 0.7, "BND": 0.2, "BIL": 0.0, "GLD": 0.1}

        table = format_rebalance_table(before, target)

        self.assertIn("종목       이전(%)   목표(%)", table)
        self.assertIn("QQQ         68.4      70.0", table)
        self.assertIn("BND         21.6      20.0", table)

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

    def test_missing_strategy_state_returns_empty_series(self):
        history = pd.DataFrame({"Portfolio": [1.0, 1.1]})

        self.assertTrue(strategy_state_series(history).empty)


if __name__ == "__main__":
    unittest.main()
