import json
import runpy
from pathlib import Path

from jira_flow.report import build_artifacts


def test_demo_is_reproducible_and_populates_the_report_without_jira(tmp_path):
    demo = runpy.run_path(str(Path(__file__).parent.parent / "examples/demo.py"))
    config, source = demo["demo_source"](tmp_path)
    assert source == demo["demo_source"](tmp_path)[1]
    assert source["metadata"]["base_url"] == "https://example.atlassian.net"
    directory = build_artifacts(source, config)
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["throughput"]["unique"] > 0
    assert metrics["lead"]["stats"]["n"] > 0
    assert metrics["accuracy"]["n"] > 0
    assert metrics["decomposition"]["stats"]["decomposed"] > 0
    assert any(row["active"] for row in metrics["status_time"])
    assert metrics["validation"]["chart_colour_checks"] is True
    assert len(list(directory.iterdir())) == 5
