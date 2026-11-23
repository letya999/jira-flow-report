"""One validated configuration for YAML, CLI overrides and Python callers."""

import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULTS = {
    "assignee_ids": [],
    "people_merge": [],
    "jira": {"project_key": None, "issue_type_ids": [], "board_id": None},
    "fields": {"priority_id": None, "assignee_id": "assignee", "estimate_id": None},
    "period": {"start": None, "end": None, "timezone": "Europe/Moscow"},
    "columns": {},
    "metrics": {
        "throughput": {"start": None, "end": {"type": "column", "id": "done"}},
        "lead_time": {
            "start": None, "end": {"type": "column", "id": "done"},
            "start_event": "first_entry", "end_event": "last_entry", "require_end_state": True,
            "histogram_bin_days": 5.0, "rolling_weeks": 4,
            "time_basis": "working", "start_by_type": {},
        },
        "cfd": {"done_column": "done"},
        "aging": {"method": "sum_visits", "time_basis": "working"},
        "decomposition": {"split_link_type_ids": []},
        "estimate_accuracy": {
            "unit": "ordinal", "scope_columns": [], "estimate_at": "first_scope_entry",
            "actual_duration": "sum_visits", "time_basis": "calendar", "frequency_bin_days": 1.0,
            "options": [],
            "working_calendar": {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "holidays": []},
        },
    },
    "output_root": "../output",
}


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Datetime must be a quoted ISO 8601 string with an offset")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Datetime requires a UTC offset: " + value)
    return result


def _merge(target, source, path=""):
    if not isinstance(source, dict):
        raise ValueError("Expected a mapping at " + (path or "root"))
    for key, value in source.items():
        here = path + str(key)
        if key not in target:
            raise ValueError("Unknown configuration setting: " + here)
        if here in ("columns", "metrics.lead_time.start_by_type"):
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict):
            if not isinstance(value, dict):
                raise ValueError("Expected a mapping at " + here)
            _merge(target[key], value, here + ".")
        else:
            target[key] = copy.deepcopy(value)


def load_config(path=None, overrides=None):
    config = copy.deepcopy(DEFAULTS)
    if path:
        file = Path(path).resolve()
        _merge(config, yaml.safe_load(file.read_text(encoding="utf-8")))
        base = file.parent
    else:
        base = Path.cwd()
        config["output_root"] = "output"
    if not isinstance(config["output_root"], str) or not config["output_root"]:
        raise ValueError("output_root must be a path")
    config["output_root"] = str((base / config["output_root"]).resolve())
    for key, value in (overrides or {}).items():
        parts = key.split(".")
        target = config
        for part in parts[:-1]:
            if not isinstance(target, dict) or part not in target:
                raise ValueError("Unknown override: " + key)
            target = target[part]
        if not isinstance(target, dict) or (parts[-1] not in target and parts[0] != "columns"
                                           and parts[:-1] != ["metrics", "lead_time", "start_by_type"]):
            raise ValueError("Unknown override: " + key)
        name = parts[-1]
        if key in ("columns", "metrics.lead_time.start_by_type"):
            target[name] = copy.deepcopy(value)
        elif isinstance(target.get(name), dict):
            if not isinstance(value, dict):
                raise ValueError("Expected a mapping at " + key)
            _merge(target[name], value, key + ".")
        else:
            target[name] = copy.deepcopy(value)
    if not isinstance(config["output_root"], str) or not config["output_root"]:
        raise ValueError("output_root must be a path")
    config["output_root"] = str(Path(config["output_root"]).resolve())
    # Keep existing saved configurations readable; new exports use named status objects.
    if isinstance(config["columns"], dict):
        for key, column in list(config["columns"].items()):
            if isinstance(column, dict) and set(column) == {"label", "status_ids"}:
                config["columns"][key] = {"name": column["label"], "statuses": [
                    {"id": status, "name": status} for status in column["status_ids"]]}
    if config["metrics"]["aging"]["method"] == "first_entry_to_last_exit":
        config["metrics"]["aging"]["method"] = "sum_visits"
    validate(config)
    return config


def parse_overrides(items):
    result = {}
    for item in items:
        if "=" not in item:
            raise ValueError("Use --set path=value")
        key, raw = item.split("=", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        result[key] = value
    return result


def _ids(values, path, numeric=False):
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError(path + " must contain quoted IDs")
    if len(values) != len(set(values)) or (numeric and any(not v.isdigit() for v in values)):
        raise ValueError("Invalid or duplicate IDs: " + path)


def _positive(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(path + " must be a positive finite number")


def status_definitions(config):
    return [status for column in config["columns"].values() for status in column["statuses"]]


def status_aliases(config, catalog=None):
    aliases = {}
    current = {s["id"] for s in status_definitions(config)}
    for status in status_definitions(config):
        for old in status.get("legacy", []):
            if "id" in old:
                if old["id"] in aliases and aliases[old["id"]] != status["id"]:
                    raise ValueError("Conflicting legacy status ID: " + old["id"])
                aliases[old["id"]] = status["id"]
            if "name" in old:
                aliases["legacy:"+old["name"]] = status["id"]
                if "id" not in old:
                    matches = [s for s in catalog or [] if s["name"] == old["name"]]
                    scoped = [s for s in matches if s.get("scope", {}).get("type") == "PROJECT"]
                    candidates = {s["id"] for s in scoped or matches}
                    if len(candidates) > 1:
                        raise ValueError("Ambiguous legacy name in project catalog: " + old["name"])
                    if candidates:
                        source = next(iter(candidates))
                        if (source in current and source != status["id"]) or (source in aliases and aliases[source] != status["id"]):
                            raise ValueError("Conflicting legacy name in project catalog: " + old["name"])
                        if source != status["id"]:
                            aliases[source] = status["id"]
    return aliases


def assignee_aliases(config):
    return {account: (group["ids"][0], group["name"])
            for group in config["people_merge"] for account in group["ids"]}


def selected_assignee_ids(config):
    aliases = assignee_aliases(config)
    selected = set(config["assignee_ids"])
    canonical = {aliases[account][0] for account in selected if account in aliases}
    return sorted(selected | {account for account, (main, _) in aliases.items() if main in canonical})


def metric_status_ids(config):
    metrics = config["metrics"]
    return {boundary["id"] for metric in ("throughput", "lead_time")
            for boundary in (metrics[metric]["start"], metrics[metric]["end"])
            if boundary and boundary["type"] == "status"} | {
                status for statuses in metrics["lead_time"]["start_by_type"].values() for status in statuses}


def validate(c):
    _ids(c["assignee_ids"], "assignee_ids")
    merged = set()
    if not isinstance(c["people_merge"], list):
        raise ValueError("people_merge must be a list")
    for group in c["people_merge"]:
        if (not isinstance(group, dict) or set(group) != {"ids", "name"}
                or not isinstance(group["name"], str) or not group["name"].strip()):
            raise ValueError("Each people_merge group requires ids and a nonempty name")
        _ids(group["ids"], "people_merge.ids")
        if (len(group["ids"]) < 2 or merged.intersection(group["ids"])
                or {"__missing__", "__multiple__"}.intersection(group["ids"])):
            raise ValueError("people_merge requires distinct Jira accounts in non-overlapping groups")
        merged.update(group["ids"])
    jira = c["jira"]
    if not isinstance(jira["project_key"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", jira["project_key"]):
        raise ValueError("jira.project_key is required and must be a valid project key")
    _ids(jira["issue_type_ids"], "jira.issue_type_ids", numeric=True)
    if not jira["issue_type_ids"]:
        raise ValueError("jira.issue_type_ids cannot be empty")
    if jira["board_id"] is not None and (type(jira["board_id"]) is not int or jira["board_id"] <= 0):
        raise ValueError("jira.board_id must be a positive integer or null")
    for key, value in c["fields"].items():
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("fields." + key + " requires a Jira field ID, not a display name")
    if len(set(c["fields"].values())) != 3:
        raise ValueError("Priority, assignee and estimate fields must be different")
    start, end = instant(c["period"]["start"]), instant(c["period"]["end"])
    if start >= end:
        raise ValueError("period.start must precede period.end")
    try:
        ZoneInfo(c["period"]["timezone"])
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("period.timezone must be a valid IANA time zone") from None
    if not isinstance(c["columns"], dict) or not c["columns"]:
        raise ValueError("columns must define the board workflow")
    seen, historical_ids, historical_names = set(), set(), set()
    for key, column in c["columns"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(key)) or not isinstance(column, dict) or set(column) != {"name", "statuses"}:
            raise ValueError("Each column requires a stable ID key, name and statuses")
        if not isinstance(column["name"], str) or not column["name"].strip():
            raise ValueError("Column names cannot be empty")
        if not isinstance(column["statuses"], list):
            raise ValueError("Column statuses must be a list")
        for status in column["statuses"]:
            if not isinstance(status, dict) or not {"id", "name"} <= set(status) or set(status)-{"id", "name", "legacy"}:
                raise ValueError("Current statuses require id and name; legacy is optional")
            _ids([status["id"]], "status.id", numeric=True)
            if not isinstance(status["name"], str) or not status["name"].strip():
                raise ValueError("Status names cannot be empty")
            if status["id"] in seen:
                raise ValueError("Status belongs to multiple columns: " + status["id"])
            seen.add(status["id"])
            if not isinstance(status.get("legacy", []), list):
                raise ValueError("Status legacy must be a list")
            for old in status.get("legacy", []):
                if not isinstance(old, dict) or not old or set(old)-{"id", "name"}:
                    raise ValueError("Legacy statuses require id, name, or both")
                if "id" in old:
                    _ids([old["id"]], "legacy.id", numeric=True)
                    if old["id"] in historical_ids:
                        raise ValueError("Duplicate legacy status ID: " + old["id"])
                    historical_ids.add(old["id"])
                if "name" in old:
                    if not isinstance(old["name"], str) or not old["name"].strip() or old["name"] != old["name"].strip():
                        raise ValueError("Legacy status names must be exact nonempty strings")
                    if old["name"] in historical_names:
                        raise ValueError("Ambiguous legacy status name: " + old["name"])
                    historical_names.add(old["name"])
    if seen & historical_ids:
        raise ValueError("A current status cannot also be a legacy alias")
    for metric in ("throughput", "lead_time"):
        m = c["metrics"][metric]
        for side in ("start", "end"):
            boundary = m[side]
            if boundary is None and metric == "throughput" and side == "start":
                continue
            if not isinstance(boundary, dict) or set(boundary) != {"type", "id"} or boundary["type"] not in ("status", "column"):
                raise ValueError(metric + "." + side + " requires type and id")
            if not isinstance(boundary["id"], str):
                raise ValueError("Boundary IDs must be strings")
            if boundary["type"] == "column" and boundary["id"] not in c["columns"]:
                raise ValueError("Unknown boundary column: " + boundary["id"])
            if boundary["type"] == "status" and not boundary["id"].isdigit():
                raise ValueError("Status boundaries require numeric Jira IDs")
            if boundary["type"] == "status" and boundary["id"] in historical_ids:
                raise ValueError("Metric boundaries must reference current status IDs, not legacy IDs")
    lead = c["metrics"]["lead_time"]
    if lead["start_event"] not in ("first_entry", "last_entry") or lead["end_event"] not in ("first_entry", "last_entry"):
        raise ValueError("Lead events must be first_entry or last_entry")
    if type(lead["require_end_state"]) is not bool:
        raise ValueError("require_end_state must be boolean")
    _positive(lead["histogram_bin_days"], "histogram_bin_days")
    if type(lead["rolling_weeks"]) is not int or lead["rolling_weeks"] <= 0:
        raise ValueError("rolling_weeks must be a positive integer")
    done = c["metrics"]["cfd"]["done_column"]
    if done not in c["columns"] or not c["columns"][done]["statuses"]:
        raise ValueError("CFD done_column must name a nonempty configured column")
    for metric in ("lead_time", "aging"):
        if c["metrics"][metric]["time_basis"] not in ("calendar", "working"):
            raise ValueError(metric + ".time_basis must be calendar or working")
    starts = lead["start_by_type"]
    if not isinstance(starts, dict) or not starts.keys() <= set(jira["issue_type_ids"]):
        raise ValueError("lead_time.start_by_type requires configured issue type IDs")
    for type_id, status_ids in starts.items():
        _ids(status_ids, "lead_time.start_by_type." + type_id, numeric=True)
        if not status_ids or historical_ids.intersection(status_ids):
            raise ValueError("lead_time.start_by_type requires current status IDs")
    if c["metrics"]["aging"]["method"] != "sum_visits":
        raise ValueError("Unsupported aging method")
    _ids(c["metrics"]["decomposition"]["split_link_type_ids"], "metrics.decomposition.split_link_type_ids", numeric=True)
    a = c["metrics"]["estimate_accuracy"]
    if a["unit"] not in ("ordinal", "story_points", "seconds", "hours", "days"):
        raise ValueError("Unsupported estimate unit")
    if not a["scope_columns"] or any(k not in c["columns"] for k in a["scope_columns"]):
        raise ValueError("Estimate scope_columns must reference configured columns")
    if a["estimate_at"] != "first_scope_entry" or a["actual_duration"] != "sum_visits":
        raise ValueError("Estimates freeze at first_scope_entry; actual_duration is sum_visits")
    if a["time_basis"] not in ("calendar", "working"):
        raise ValueError("time_basis must be calendar or working")
    _positive(a["frequency_bin_days"], "frequency_bin_days")
    if not isinstance(a["options"], list) or (a["unit"] == "ordinal" and not a["options"]):
        raise ValueError("Ordinal estimates require ordered options")
    option_ids = []
    for option in a["options"]:
        if not isinstance(option, dict) or set(option) - {"id", "label", "value", "open_ended"} or not {"id", "label"} <= set(option):
            raise ValueError("Estimate options require id/label; optional value/open_ended")
        option_ids.append(option["id"])
        if a["unit"] != "ordinal":
            _positive(option.get("value"), "estimate option value")
        if "open_ended" in option and type(option["open_ended"]) is not bool:
            raise ValueError("open_ended must be boolean")
    _ids(option_ids, "estimate options", numeric=True)
    calendar = a["working_calendar"]
    if not calendar["weekdays"] or any(type(d) is not int or d not in range(7) for d in calendar["weekdays"]):
        raise ValueError("working_calendar.weekdays must contain 0..6")
    from datetime import date, time
    if time.fromisoformat(calendar["start"]) >= time.fromisoformat(calendar["end"]):
        raise ValueError("Working hours must be within the same day, start before end")
    for holiday in calendar["holidays"]:
        date.fromisoformat(holiday)
    if not isinstance(c["output_root"], str) or not c["output_root"]:
        raise ValueError("output_root must be a path")
