"""Validate commit subjects locally and in CI."""

from pathlib import Path
import re
import subprocess
import sys


SUBJECT = re.compile(
    r"^(?:feat|fix|docs|refactor|perf|test|chore|build|ci|style|revert)"
    r"(?:\([^()\r\n]+\))?: \S.*$"
)


def main():
    if sys.argv[1:] == ["--all"]:
        subjects = subprocess.check_output(
            ["git", "log", "--format=%s", "HEAD"], encoding="utf-8"
        ).splitlines()
    elif len(sys.argv) == 2:
        lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
        subjects = [lines[0] if lines else ""]
    else:
        print("Usage: check_commit_messages.py MESSAGE_FILE | --all", file=sys.stderr)
        return 2
    invalid = [subject for subject in subjects if not SUBJECT.fullmatch(subject)]
    for subject in invalid:
        print(f"Invalid commit subject: {subject!r}", file=sys.stderr)
    if invalid:
        print("Use 'type: description', for example 'fix: preserve cached results'.", file=sys.stderr)
    return int(bool(invalid))


if __name__ == "__main__":
    raise SystemExit(main())
