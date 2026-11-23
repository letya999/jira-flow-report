import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jira_flow.config import load_config


BASE = datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=3)))


@pytest.fixture
def config(tmp_path):
    return load_config(Path(__file__).parent.parent/"configs/example.yaml", {
        "assignee_ids": ["A"], "jira.project_key": "TEST", "jira.issue_type_ids": ["1"], "jira.board_id": None,
        "period.start": BASE.isoformat(), "period.end": (BASE+timedelta(days=21)).isoformat(),
        "period.timezone": "Europe/Moscow",
        "columns": {"todo": {"label": "Todo", "status_ids": ["1"]},
                    "development": {"label": "Work", "status_ids": ["2", "5"]},
                    "review": {"label": "Review", "status_ids": ["3"]},
                    "done": {"label": "Done", "status_ids": ["4"]}},
        "metrics.lead_time.time_basis": "calendar", "metrics.aging.time_basis": "calendar",
        "metrics.lead_time.start.id": "2", "metrics.lead_time.end.id": "4",
        "metrics.decomposition.split_link_type_ids": [],
        "metrics.estimate_accuracy.options": [{"id": "10", "label": "S"}, {"id": "20", "label": "M"}],
        "output_root": str(tmp_path/"output"),
    })


def issue(config, key="TEST-1", events=None, created=-1):
    if events is None:
        events = [(1, "2"), (2, "5"), (3, "3"), (4, "2"), (5, "3"), (6, "4"), (9, "2"), (10, "3"), (13, "4")]
    state, history = "1", []
    for n, (day, target) in enumerate(events, 1):
        history.append({"at": (BASE+timedelta(days=day)).isoformat(), "history_id": str(n),
                        "from": state, "from_label": state, "to": target, "to_label": target})
        state = target
    fields = config["fields"]
    return {"id": key.split("-")[-1], "key": key, "summary": "Fixture issue",
            "project_key": "TEST", "issue_type": {"id": "1", "name": "Task"},
            "created_at": (BASE+timedelta(days=created)).isoformat(),
            "updated_at": (BASE+timedelta(days=30)).isoformat(), "resolution_at": None, "closed_at": None,
            "current_status": {"id": state, "name": state}, "status_history": history,
            "field_values": {fields["assignee_id"]: {"accountId": "B", "displayName": "Current B"},
                             fields["priority_id"]: {"id": "p2", "value": "Current priority"},
                             fields["estimate_id"]: [{"id": "20", "value": "M"}]},
            "field_history": {
                fields["assignee_id"]: [{"at": (BASE+timedelta(days=30)).isoformat(), "history_id": "30",
                                         "from": "A", "from_label": "Owner A", "to": "B", "to_label": "Current B"}],
                fields["priority_id"]: [{"at": (BASE+timedelta(days=25)).isoformat(), "history_id": "25",
                                         "from": "p1", "from_label": "Old priority", "to": "p2", "to_label": "Current priority"}],
                fields["estimate_id"]: [{"at": (BASE+timedelta(days=4)).isoformat(), "history_id": "4",
                                         "from": "[10]", "from_label": "S", "to": "[20]", "to_label": "M"}],
            }}


def snapshot(config, issues):
    return {"schema_version": 1, "metadata": {
        "base_url": "https://example.atlassian.net", "fetched_at": (BASE+timedelta(days=31)).isoformat(),
        "field_metadata": {key: {"id": key, "schema": {"type": "user" if key == "assignee" else "option"}}
                           for key in config["fields"].values()},
        "coverage": {"project_key": "TEST", "issue_type_ids": ["1"], "assignee_ids": ["A"],
                     "field_ids": list(config["fields"].values()), "period_end": config["period"]["end"]}},
        "issues": copy.deepcopy(issues)}
