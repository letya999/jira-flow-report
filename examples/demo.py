"""Build the real report from deterministic, entirely fictional Jira histories."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

from jira_flow.config import load_config
from jira_flow.report import build_artifacts


ROOT = Path(__file__).resolve().parent.parent
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 4, 1, tzinfo=timezone.utc)
PEOPLE = ["Alex Morgan", "Jordan Lee", "Sam Taylor", "Casey Chen"]
STATUSES = {"1": "To do", "3": "Refinement", "4": "Development", "7": "Code review",
            "8": "Production", "10": "Check", "11": "Done"}
SUMMARIES = ["Add search filters", "Improve checkout validation", "Reduce API response time",
             "Update notification settings", "Fix timezone handling", "Simplify export workflow",
             "Add keyboard navigation", "Improve import diagnostics"]


def demo_source(output_root):
    config = load_config(ROOT / "configs/example.yaml", {"output_root": str(output_root)})
    # Fixed seed keeps synthetic screenshots reproducible.
    rng, issues, nodes = Random(42), [], []  # nosec B311
    fields = config["fields"]
    for i in range(96):
        key, issue_id = f"DEMO-{i + 1}", str(i + 1)
        created = START + timedelta(days=-18 + i * 1.05)
        status, at, history = "1", created, []
        durations = [("3", rng.uniform(1, 7)), ("4", rng.uniform(1, 8)),
                     ("7", rng.uniform(2, 14) * (2.5 if i % 13 == 0 else 1)),
                     ("8", rng.uniform(.15, 3)), ("10", rng.uniform(.2, 2)),
                     ("11", rng.uniform(.5, 4))]
        if i % 11 == 0:
            durations.insert(3, ("4", .5))
            durations.insert(4, ("7", 4))
        for target, days in durations:
            at += timedelta(days=days)
            if at >= END or (i % 19 == 0 and len(history) >= 2):
                break
            history.append({"at": at.isoformat(), "history_id": str(len(history) + 1),
                            "from": status, "from_label": STATUSES[status],
                            "to": target, "to_label": STATUSES[target]})
            status = target
        person, priority = i % 4, i % 3
        estimate = config["metrics"]["estimate_accuracy"]["options"][i % 5]
        closed = history[-1]["at"] if status == "11" else None
        issues.append({"id": issue_id, "key": key, "summary": SUMMARIES[i % 8] + f" · {i + 1}",
                       "project_key": "DEMO", "issue_type": {"id": str(i % 3 + 1),
                                                               "name": ["Story", "Task", "Bug"][i % 3]},
                       "created_at": created.isoformat(),
                       "updated_at": history[-1]["at"] if history else created.isoformat(),
                       "resolution_at": closed, "closed_at": closed,
                       "current_status": {"id": status, "name": STATUSES[status]},
                       "status_history": history, "change_timestamps": [h["at"] for h in history],
                       "field_values": {
                           fields["assignee_id"]: {"accountId": f"demo-person-{person}", "displayName": PEOPLE[person]},
                           fields["priority_id"]: {"id": f"demo-priority-{priority}",
                                                    "value": ["High", "Medium", "Low"][priority]},
                           fields["estimate_id"]: {"id": estimate["id"], "value": estimate["label"]}},
                       "field_history": {field: [] for field in fields.values()}})
        children = [str(1000 + i * 3 + c) for c in range(2)] if i % 4 == 1 else []
        nodes.append({"id": issue_id, "key": key, "created_at": created.isoformat(),
                      "parent_id": None, "subtask_ids": children, "links": [], "changes": []})
        nodes.extend({"id": child, "key": "DEMO-" + child,
                      "created_at": (created + timedelta(days=1)).isoformat(),
                      "parent_id": issue_id, "subtask_ids": [], "links": [], "changes": []} for child in children)
    source = {"schema_version": 1, "metadata": {
        "base_url": "https://example.atlassian.net", "project_name": "Demo delivery team (synthetic)",
        "fetched_at": END.isoformat(),
        "status_categories": {s: "done" if s == "11" else "indeterminate" for s in STATUSES},
        "field_metadata": {field: {"id": field, "name": name, "schema": {"type": kind}}
                           for field, name, kind in [(fields["assignee_id"], "Assignee", "user"),
                                                     (fields["priority_id"], "Business priority", "option"),
                                                     (fields["estimate_id"], "T-shirt size", "option")]},
        "coverage": {"project_key": "DEMO", "field_ids": list(fields.values()), "assignee_ids": [],
                     "issue_type_ids": config["jira"]["issue_type_ids"], "period_end": config["period"]["end"]}},
        "issues": issues, "hierarchy": {"nodes": nodes, "link_types": [], "verified_project_count": len(nodes)}}
    return config, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    config, source = demo_source(args.output_root.resolve())
    print(json.dumps({"output": str(build_artifacts(source, config)), "synthetic": True}))


if __name__ == "__main__":
    main()
