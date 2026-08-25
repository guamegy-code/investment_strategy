import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.point_in_time_signals import (  # noqa: E402
    load_point_in_time_signal,
    materialize_as_of,
    to_ohlcv_signal,
)


class PointInTimeSignalTests(unittest.TestCase):
    def write_signal(self, rows):
        handle = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", newline=""
        )
        path = Path(handle.name)
        handle.close()
        pd.DataFrame(rows).to_csv(path, index=False)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_signal_is_not_visible_before_available_date(self):
        path = self.write_signal([
            {
                "ObservationDate": "2023-01-01",
                "AvailableDate": "2023-01-03",
                "Value": 10,
                "MethodologyVersion": "v1",
            },
            {
                "ObservationDate": "2023-01-04",
                "AvailableDate": "2023-01-06",
                "Value": 20,
                "MethodologyVersion": "v1",
            },
        ])
        signal = load_point_in_time_signal(
            path,
            minimum=0,
            maximum=100,
            expected_methodology_version="v1",
        )
        result = materialize_as_of(
            signal,
            pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-05", "2023-01-06"]),
        )

        self.assertTrue(pd.isna(result.loc["2023-01-02", "Value"]))
        self.assertEqual(result.loc["2023-01-03", "Value"], 10)
        self.assertEqual(result.loc["2023-01-05", "Value"], 10)
        self.assertEqual(result.loc["2023-01-06", "Value"], 20)

    def test_rejects_availability_before_observation(self):
        path = self.write_signal([{
            "ObservationDate": "2023-01-04",
            "AvailableDate": "2023-01-03",
            "Value": 50,
            "MethodologyVersion": "v1",
        }])

        with self.assertRaisesRegex(ValueError, "cannot precede"):
            load_point_in_time_signal(path)

    def test_rejects_unexpected_methodology_version(self):
        path = self.write_signal([{
            "ObservationDate": "2023-01-03",
            "AvailableDate": "2023-01-03",
            "Value": 50,
            "MethodologyVersion": "v2",
        }])

        with self.assertRaisesRegex(ValueError, "Unexpected methodology"):
            load_point_in_time_signal(
                path,
                expected_methodology_version="v1",
            )

    def test_ohlcv_adapter_preserves_availability_metadata(self):
        path = self.write_signal([{
            "ObservationDate": "2023-01-03",
            "AvailableDate": "2023-01-03",
            "Value": 42,
            "MethodologyVersion": "breadth-v1",
        }])
        signal = load_point_in_time_signal(path)
        result = to_ohlcv_signal(signal, pd.to_datetime(["2023-01-03"]))

        self.assertEqual(result.loc["2023-01-03", "Close"], 42)
        self.assertEqual(
            result.loc["2023-01-03", "MethodologyVersion"],
            "breadth-v1",
        )


if __name__ == "__main__":
    unittest.main()
