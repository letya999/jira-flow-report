"""Enforce the project's commit format and prohibition on coauthor trailers."""

import re
import sys
from pathlib import Path


def check_message(message):
    lines = message.splitlines()
    if re.search(r"^\s*co-authored-by\s*:", message, re.IGNORECASE | re.MULTILINE):
        return "Co-authored-by trailers are prohibited."
    if not lines or not re.fullmatch(
        r"(?:feat|fix|refactor|docs|test|chore|perf)(?:\([a-z0-9_-]+\))?!?: \S.*", lines[0]
    ):
        return "Use type(scope): description; allowed types: feat, fix, refactor, docs, test, chore, perf."
    return None


if __name__ == "__main__":
    error = check_message(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    if error:
        print(error, file=sys.stderr)
    raise SystemExit(1 if error else 0)
