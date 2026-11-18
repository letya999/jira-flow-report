"""Generate one self-contained report and four accompanying data/config files."""

import csv
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import yaml

from . import __version__
from .config import instant, load_config
from .colours import checked_palette
from .decomposition import calculate_decomposition
from .jira_client import JiraClient, export_issue
from .metrics import calculate


def _check_coverage(snapshot, config):
    coverage = snapshot["metadata"]["coverage"]
    if coverage["project_key"] != config["jira"]["project_key"]:
        raise ValueError("Snapshot covers a different Jira project")
    if instant(coverage["period_end"]) != instant(config["period"]["end"]):
        raise ValueError("Changing the cutoff requires a fresh run: exported membership is fixed at period end")
    if not set(config["jira"]["issue_type_ids"]) <= set(coverage["issue_type_ids"]):
        raise ValueError("Snapshot does not cover the requested issue types")
    previous, requested = coverage["assignee_ids"], config["assignee_ids"]
    if previous and (not requested or not set(requested) <= set(previous)):
        raise ValueError("Snapshot does not cover the requested assignees")
    if not set(config["fields"].values()) <= set(coverage["field_ids"]):
        raise ValueError("Snapshot does not contain the requested field IDs; fetch again")
    meta = snapshot["metadata"]["field_metadata"]
    if meta[config["fields"]["assignee_id"]].get("schema", {}).get("type") != "user":
        raise ValueError("The assignee field must be a single-user field")
    if "status_ids" in snapshot["metadata"]:
        configured = {s for column in config["columns"].values() for s in column["status_ids"]}
        configured.update(boundary["id"] for metric in ("throughput", "lead_time")
                          for boundary in (config["metrics"][metric]["start"], config["metrics"][metric]["end"])
                          if boundary and boundary["type"] == "status")
        if configured-set(snapshot["metadata"]["status_ids"]):
            raise ValueError("Snapshot does not cover the configured status IDs")


def _csv_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, issues, metrics):
    leads = {row["key"]: row for row in metrics["lead"]["tasks"]}
    estimates = {row["key"]: row for row in metrics["accuracy"]["tasks"]}
    decomposition = {row["key"]: row for row in metrics["decomposition"].get("tasks", [])}
    columns = ["key", "summary", "issue_type_id", "created_at", "updated_at", "closed_at", "resolution_at",
               "current_status_id", "current_status_name", "period_end_status_id", "period_end_status_name",
               "assignee_id", "assignee_name", "priority_id", "priority_name", "estimate_before_start",
               "lead_days", "direct_children", "descendants", "decomposition_depth", "child_keys",
               "field_values", "status_history", "field_history", "change_timestamps"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for issue in issues:
            row = {k: issue.get(k) for k in ("key", "summary", "created_at", "updated_at", "closed_at", "resolution_at")}
            row.update({"issue_type_id": issue["issue_type"]["id"],
                        "current_status_id": issue["current_status"]["id"], "current_status_name": issue["current_status"]["name"],
                        "period_end_status_id": issue["status_at_period_end"]["id"], "period_end_status_name": issue["status_at_period_end"]["name"],
                        "assignee_id": issue["assignee_at_period_end"]["id"], "assignee_name": issue["assignee_at_period_end"]["name"],
                        "priority_id": issue["priority_at_period_end"]["id"], "priority_name": issue["priority_at_period_end"]["name"],
                        "estimate_before_start": estimates.get(issue["key"], {}).get("estimate"),
                        "lead_days": leads.get(issue["key"], {}).get("days"),
                        "direct_children": decomposition.get(issue["key"], {}).get("direct"),
                        "descendants": decomposition.get(issue["key"], {}).get("descendants"),
                        "decomposition_depth": decomposition.get(issue["key"], {}).get("depth"),
                        "child_keys": decomposition.get(issue["key"], {}).get("children"),
                        "field_values": issue["field_values"],
                        "status_history": issue["status_history"], "field_history": issue["field_history"],
                        "change_timestamps": issue.get("change_timestamps")})
            writer.writerow({k: _csv_value(v) for k, v in row.items()})


def render(metrics, config, snapshot):
    package = files("jira_flow")
    payload = {**metrics, "config": config, "base_url": snapshot["metadata"]["base_url"],
               "field_names": {key: value.get("name", key) for key, value in snapshot["metadata"]["field_metadata"].items()},
               "fetched_at": snapshot["metadata"]["fetched_at"]}
    html = package.joinpath("templates/report.html").read_text(encoding="utf-8")
    replacements = {
        "__REPORT_DATA__": json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c"),
        "__CHART_HELPERS__": package.joinpath("assets/charts.js").read_text(encoding="utf-8"),
        "__FONTS__": package.joinpath("assets/fonts.css").read_text(encoding="utf-8"),
    }
    for marker, value in replacements.items():
        if html.count(marker) != 1:
            raise ValueError("Incorrect HTML template marker: " + marker)
        html = html.replace(marker, value)
    return html


def build_artifacts(snapshot, config):
    _check_coverage(snapshot, config)
    metrics = calculate(snapshot["issues"], config, snapshot["metadata"])
    metrics["decomposition"] = calculate_decomposition(snapshot.get("hierarchy"), metrics, config)
    selected = {task["key"] for task in metrics["tasks"]}
    issues = [i for i in snapshot["issues"] if i["key"] in selected]
    snapshot = {**snapshot, "issues": issues}
    snapshot["metadata"] = {**snapshot["metadata"], "coverage": {
        **snapshot["metadata"]["coverage"], "assignee_ids": config["assignee_ids"],
        "issue_type_ids": config["jira"]["issue_type_ids"],
    }}
    metrics["generator_version"] = __version__
    metrics["source_sha256"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    metrics["config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    metrics["field_ids"] = config["fields"]
    metrics["validation"]["complete_histories"] = True
    metrics["chart_colours"] = {kind: checked_palette(metrics["cfd_"+kind]) for kind in ("columns", "statuses")}
    metrics["validation"]["chart_colour_checks"] = True
    mapping = {frozenset(col["status_ids"]) for col in config["columns"].values()}
    board = snapshot["metadata"].get("board")
    if board:
        actual = {frozenset(str(s["id"]) for s in col["statuses"]) for col in board["columnConfig"]["columns"]}
        metrics["validation"]["current_board_mapping_matches"] = mapping == actual
    html = render(metrics, config, snapshot)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    key = config["jira"]["project_key"]
    root = Path(config["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (key + "-" + stamp)
    if destination.exists():
        raise ValueError("Run directory already exists")
    with tempfile.TemporaryDirectory(prefix="."+key+"-", dir=root) as temp:
        stage = Path(temp)
        (stage/"issues.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        (stage/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        header = ("# Resolved configuration for this run; contains no credentials.\n"
                  "# Match Jira fields/statuses/options by ID, not by display label.\n"
                  "# period.start is inclusive; period.end is exclusive.\n"
                  "# CLI overrides: --set dotted.path=value; lists/objects use JSON.\n"
                  "# See configs/example.yaml for the full English filling guide.\n")
        (stage/f"config_{key}_{stamp}.yaml").write_text(header+yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        write_csv(stage/"issues.csv", issues, metrics)
        (stage/"report.html").write_text(html, encoding="utf-8")
        stage.rename(destination)
    return destination


def run_report(config_path=None, overrides=None, input_path=None, env_file=".env"):
    config = load_config(config_path, overrides)
    if input_path:
        snapshot = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if snapshot.get("schema_version") != 1:
            raise ValueError("Unsupported issue snapshot schema")
    else:
        client = JiraClient(env_file=env_file)
        source = client.fetch(config)
        snapshot = {"schema_version": 1, "metadata": {
            "base_url": source["base_url"], "fetched_at": source["fetched_at"], "query": source["query"],
            "project_name": source["project_name"], "status_categories": source["status_categories"],
            "field_metadata": source["field_metadata"], "board": source["board"], "status_ids": source["status_ids"],
            "coverage": {"project_key": config["jira"]["project_key"], "field_ids": list(config["fields"].values()),
                         "assignee_ids": config["assignee_ids"], "issue_type_ids": config["jira"]["issue_type_ids"],
                         "period_end": config["period"]["end"]}},
            "issues": [export_issue(record, config) for record in source["records"]],
            "hierarchy": client.fetch_hierarchy(config)}
    return build_artifacts(snapshot, config)
