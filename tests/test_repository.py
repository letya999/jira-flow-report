import runpy
from pathlib import Path

import pytest


CHECK_MESSAGE = runpy.run_path(str(Path(__file__).parent.parent / ".githooks/check_commit.py"))["check_message"]
IS_FORBIDDEN = runpy.run_path(str(
    Path(__file__).parent.parent / ".repo-safety/scripts/forbid_sensitive_files.py"
))["is_forbidden"]


@pytest.mark.parametrize("message", [
    "feat: add report", "fix(metrics): correct cutoff", "refactor!: change export schema",
    "docs: описать запуск\n\nDetails without attribution.",
])
def test_commit_format_accepts_supported_messages(message):
    assert CHECK_MESSAGE(message) is None


@pytest.mark.parametrize("message", [
    "", "initial commit", "merge: branch", "feat: ",
    "feat: add report\n\nCo-authored-by: Example <example@example.com>",
    "docs: update\n\n  cO-aUtHoReD-bY: Example <example@example.com>\n",
])
def test_commit_format_rejects_invalid_subjects_and_coauthors(message):
    assert CHECK_MESSAGE(message) is not None


@pytest.mark.parametrize("path, forbidden", [
    ("configs/team.yaml", True), ("configs/person/settings.yaml", True),
    ("output/.env.example", True), ("output/report.html", True), (".cache/issues.json", True),
    ("dist/report.whl", True), (".env", True), (".env.production", True),
    ("configs/example.yaml", False), (".env.example", False), ("src/jira_flow/config.py", False),
])
def test_publication_path_guard(path, forbidden):
    assert IS_FORBIDDEN(path) is forbidden
