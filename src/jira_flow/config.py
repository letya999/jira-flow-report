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
        },
        "cfd": {"done_column": "done"},
        "aging": {"method": "first_entry_to_last_exit"},
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
        if here == "columns":
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
        if not isinstance(target, dict) or (parts[-1] not in target and parts[0] != "columns"):
            raise ValueError("Unknown override: " + key)
        name = parts[-1]
        if key == "columns":
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


def validate(c):
    _ids(c["assignee_ids"], "assignee_ids")
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
    seen = set()
    for key, column in c["columns"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(key)) or not isinstance(column, dict) or set(column) != {"label", "status_ids"}:
            raise ValueError("Each column requires a stable key, label and status_ids")
        if not isinstance(column["label"], str) or not column["label"].strip():
            raise ValueError("Column labels cannot be empty")
        _ids(column["status_ids"], "columns." + key + ".status_ids", numeric=True)
        for status in column["status_ids"]:
            if status in seen:
                raise ValueError("Status belongs to multiple columns: " + status)
            seen.add(status)
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
    lead = c["metrics"]["lead_time"]
    if lead["start_event"] not in ("first_entry", "last_entry") or lead["end_event"] not in ("first_entry", "last_entry"):
        raise ValueError("Lead events must be first_entry or last_entry")
    if type(lead["require_end_state"]) is not bool:
        raise ValueError("require_end_state must be boolean")
    _positive(lead["histogram_bin_days"], "histogram_bin_days")
    if type(lead["rolling_weeks"]) is not int or lead["rolling_weeks"] <= 0:
        raise ValueError("rolling_weeks must be a positive integer")
    done = c["metrics"]["cfd"]["done_column"]
    if done not in c["columns"] or not c["columns"][done]["status_ids"]:
        raise ValueError("CFD done_column must name a nonempty configured column")
    if c["metrics"]["aging"]["method"] not in ("first_entry_to_last_exit", "sum_visits"):
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
