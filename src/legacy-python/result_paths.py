"""Locate research reports consistently for both producers and consumers."""

from pathlib import Path

from config import RESEARCH_RESULT_DIR


def research_result_path(filename):
    """Group reports by their existing filename namespace, such as strategy30."""
    filename = Path(filename)
    if filename.is_absolute() or len(filename.parts) != 1:
        raise ValueError("Research report names must be a single relative filename")
    topic = filename.name.split("_", 1)[0].lower()
    directory = RESEARCH_RESULT_DIR / topic
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename
