from datetime import timedelta

from jira_flow.decomposition import Hierarchy, calculate_decomposition, export_hierarchy
from conftest import BASE


def at(day):
    return (BASE+timedelta(days=day)).isoformat()


def node(number, parent=None, created=0, children=None, links=None, changes=None):
    return {"id": str(number), "key": f"TEST-{number}", "summary": "Structural issue",
            "created_at": at(created), "updated_at": at(30), "issue_type": {},
            "parent_id": str(parent) if parent else None, "subtask_ids": children or [],
            "links": links or [], "changes": changes or []}


SPLIT = [{"id": "10", "outward": "splits into", "inward": "split from"}]


def graph(nodes):
    return {"nodes": nodes, "link_types": SPLIT, "verified_project_count": len(nodes)}


def test_tree_cutoff_reparenting_split_removal_dedup_and_depth():
    nodes = [node(1, parent=9, links=[{"type_id": "10", "outward_id": "3", "inward_id": None},
                                    {"type_id": "10", "outward_id": "4", "inward_id": None}], changes=[
        {"at": at(25), "id": "2", "items": [{"field": "Link", "to": "TEST-4", "toString": "This issue splits into TEST-4"}]},
        {"at": at(26), "id": "3", "items": [{"field": "Link", "from": "TEST-5", "fromString": "This issue splits into TEST-5"}]}]),
        node(2), node(3, parent=2, changes=[{"at": at(25), "id": "1", "items": [
            {"field": "IssueParentAssociation", "from": "1", "to": "2"}]}]),
        node(4), node(5), node(6, parent=5), node(7, parent=1, created=22), node(9)]
    tree = Hierarchy(graph(nodes), ["10"])
    measured = tree.measure("TEST-1", BASE+timedelta(days=21))
    assert measured == {"direct": 2, "descendants": 3, "depth": 2,
                        "children": ["TEST-3", "TEST-5"], "problems": []}
    assert tree.measure("TEST-2", BASE+timedelta(days=21))["direct"] == 0
    assert tree.measure("TEST-4", BASE+timedelta(days=21))["direct"] == 0
    # An issue's own epic parent never makes that issue decomposed.
    assert tree.measure("TEST-6", BASE+timedelta(days=21))["depth"] == 0


def test_cycles_and_missing_children_are_unknown_not_zero():
    data = graph([node(1, parent=2), node(2, parent=1), node(3, children=["404"])])
    tree = Hierarchy(data, [])
    for key in ("TEST-1", "TEST-3", "TEST-404"):
        measured = tree.measure(key, BASE+timedelta(days=21))
        assert measured["direct"] is None and measured["problems"]


def test_cohort_means_and_open_large_tasks_use_matching_status_baselines(config):
    tasks = [{"key": f"TEST-{i}", "person_id": "A", "person": "Owner", "summary": "Task"} for i in (1, 2, 4, 5)]
    metrics = {"end": at(21), "tasks": tasks, "people": [{"id": "A", "name": "Owner"}],
               "updated_in_period": {"keys": ["TEST-1", "TEST-4", "TEST-5"]},
               "status_time": [{"name": "Work", "mean": 5, "active": [
                   {**tasks[0], "days": 10, "entered_at": at(11)},
                   {**tasks[1], "days": 6, "entered_at": at(15)},
                   {**tasks[3], "days": 5, "entered_at": at(16)}]}],
               "lead": {"stats": {"mean": 10}, "tasks": [{**tasks[2], "done": at(12), "days": 15}]}}
    data = graph([node(1), node(2), node(3, parent=4, created=13), node(4), node(5), node(6, parent=1)])
    result = calculate_decomposition(data, metrics, config)
    assert result["cohort_n"] == 4  # Includes old unchanged open TEST-2.
    assert result["stats"] == {"n": 4, "decomposed": 2, "share": .5, "mean_children": .5,
                               "mean_children_decomposed": 1, "mean_depth": .5, "max_depth": 1}
    assert [t["key"] for t in result["open_large"]] == ["TEST-2"]
    assert result["open_large"][0]["ratio"] == 1.2
    assert result["open_large"][0]["entered_at"] == at(15)
    assert "closed_large" not in result and "closed_excluded" not in result
    assert not calculate_decomposition(None, metrics, config)["available"]


def test_export_preserves_old_structural_events_without_field_ids():
    data = export_hierarchy([{"id": "1", "key": "TEST-1", "fields": {
        "summary": "Root", "created": at(0), "updated": at(30), "issuetype": {"id": "1"}}}],
        {"1": [{"id": "1", "created": at(25), "items": [
            {"field": "IssueParentAssociation", "from": "2", "to": None},
            {"fieldId": "summary", "field": "summary", "fromString": "Old"}]}]}, SPLIT, 1)
    assert len(data["nodes"][0]["changes"][0]["items"]) == 1
