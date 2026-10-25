"""Shared import setup for unittest discovery and focused test runs."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE_DIR = PROJECT_ROOT / "src" / "legacy-python"
if str(LEGACY_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_SOURCE_DIR))
