"""Point-in-time contracts for externally published market signals."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "ObservationDate",
    "AvailableDate",
    "Value",
    "MethodologyVersion",
}


def load_point_in_time_signal(
    path,
    *,
    minimum=None,
    maximum=None,
    expected_methodology_version=None,
):
    """Load and validate an external series without hiding publication lag."""
    frame = pd.read_csv(Path(path))
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(
            "Point-in-time signal is missing columns: " + ", ".join(missing)
        )

    frame = frame.copy()
    for column in ("ObservationDate", "AvailableDate"):
        frame[column] = pd.to_datetime(frame[column], errors="raise")
        if frame[column].dt.tz is not None:
            frame[column] = frame[column].dt.tz_localize(None)
    frame["Value"] = pd.to_numeric(frame["Value"], errors="raise")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("Point-in-time signal contains missing required values")
    if frame["ObservationDate"].duplicated().any():
        raise ValueError("ObservationDate must be unique")
    if (frame["AvailableDate"] < frame["ObservationDate"]).any():
        raise ValueError("AvailableDate cannot precede ObservationDate")
    if minimum is not None and (frame["Value"] < minimum).any():
        raise ValueError(f"Signal values must be at least {minimum}")
    if maximum is not None and (frame["Value"] > maximum).any():
        raise ValueError(f"Signal values must be at most {maximum}")
    if expected_methodology_version is not None:
        versions = set(frame["MethodologyVersion"].astype(str))
        if versions != {str(expected_methodology_version)}:
            raise ValueError(
                "Unexpected methodology versions: "
                + ", ".join(sorted(versions))
            )
    return frame.sort_values(["AvailableDate", "ObservationDate"])


def materialize_as_of(signal, calendar):
    """Expose each observation starting on, and never before, AvailableDate."""
    dates = pd.DatetimeIndex(calendar).tz_localize(None).sort_values().unique()
    left = pd.DataFrame({"Date": dates})
    right = signal.sort_values(
        ["AvailableDate", "ObservationDate"]
    ).drop_duplicates("AvailableDate", keep="last")
    materialized = pd.merge_asof(
        left,
        right,
        left_on="Date",
        right_on="AvailableDate",
        direction="backward",
        allow_exact_matches=True,
    ).set_index("Date")
    materialized.index.name = "Date"
    return materialized


def to_ohlcv_signal(signal, calendar):
    """Materialize a validated scalar series for the existing backtest loader."""
    materialized = materialize_as_of(signal, calendar)
    result = pd.DataFrame(index=materialized.index)
    for column in ("Open", "High", "Low", "Close"):
        result[column] = materialized["Value"]
    result["Volume"] = 0.0
    result["ObservationDate"] = materialized["ObservationDate"]
    result["AvailableDate"] = materialized["AvailableDate"]
    result["MethodologyVersion"] = materialized["MethodologyVersion"]
    return result
