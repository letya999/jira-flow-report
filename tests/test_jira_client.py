import pytest

from jira_flow.jira_client import JiraClient


def test_bulk_pages_can_split_one_changelog_group():
    client = JiraClient.__new__(JiraClient)
    pages = iter([
        {"nextPageToken": "next", "issueChangeLogs": [{"issueId": "1", "changeHistories": [
            {"id": "10", "created": 1788220800000, "items": [{"fieldId": "status"}]}]}]},
        {"issueChangeLogs": [{"issueId": "1", "changeHistories": [
            {"id": "10", "created": "2026-09-01T00:00:00+00:00", "items": [{"fieldId": "status"}, {"field": "Link"}]}]}]},
    ])
    client._request = lambda *a, **kw: next(pages)
    rows = client.bulk_changelog(["1", "2"])
    assert rows["2"] == []
    assert len(rows["1"]) == 1
    assert rows["1"][0]["items"] == [{"fieldId": "status"}, {"field": "Link"}]


def test_search_rejects_duplicate_results():
    client = JiraClient.__new__(JiraClient)
    client.get = lambda *a: {"isLast": True, "issues": [{"id": "1", "key": "TEST-1"}]*2}
    with pytest.raises(ValueError, match="Duplicate"):
        client.search("project = TEST", ["updated"])


def test_project_status_catalog_includes_unused_statuses_and_checks_pages():
    client = JiraClient.__new__(JiraClient)
    calls = []
    pages = iter([
        {"startAt": 0, "total": 2, "values": [{"id": "1", "name": "Current"}], "isLast": False},
        {"startAt": 1, "total": 2, "values": [{"id": "9", "name": "Unused"}], "isLast": True},
    ])
    def get(path, params):
        calls.append((path, params))
        return next(pages)
    client.get = get
    rows, complete = client.status_catalog("100", [])
    assert complete is True
    assert [row["id"] for row in rows] == ["1", "9"]
    assert [p["startAt"] for _, p in calls] == [0, 1]
    assert all(p["projectId"] == "100" and p["includeGlobalStatuses"] == "true" for _, p in calls)


def test_status_catalog_permission_fallback_does_not_swallow_connection_errors():
    client = JiraClient.__new__(JiraClient)
    def forbidden(*args):
        raise RuntimeError("Jira HTTP 403: /rest/api/3/statuses/search")
    client.get = forbidden
    assert client.status_catalog("100", [{"statuses": [{"id": "1", "name": "Current"}]}]) == (
        [{"id": "1", "name": "Current"}], False)
    def unavailable(*args):
        raise RuntimeError("Jira connection failed")
    client.get = unavailable
    with pytest.raises(RuntimeError, match="connection failed"):
        client.status_catalog("100", [])



def test_fetch_expands_assignee_aliases_in_jql(config):
    config["people_merge"] = [{"ids": ["A", "A2"], "name": "Unified owner"}]
    config["assignee_ids"] = ["A2"]
    client = JiraClient.__new__(JiraClient)
    client.base = "https://example.atlassian.net"
    statuses = [{"id": s["id"]} for col in config["columns"].values() for s in col["statuses"]]
    responses = {
        "/rest/api/3/field": [{"id": f, "schema": {"type": "user"}} for f in config["fields"].values()],
        "/rest/api/3/project/TEST": {"id": "123", "name": "Test", "issueTypes": [{"id": "1"}]},
        "/rest/api/3/myself": {"timeZone": "Europe/Moscow"},
        "/rest/api/3/search/jql": {"isLast": True, "issues": []},
        "/rest/api/3/project/TEST/statuses": [{"statuses": statuses}],
        "/rest/api/3/statuses/search": {"startAt": 0, "total": len(statuses), "values": statuses, "isLast": True},
    }
    requests = []
    def get(path, params=None):
        requests.append((path, params))
        return responses[path]

    client.get = get
    source = client.fetch(config)
    assert 'assignee in ("A","A2") OR updated >=' in source["query"]
    assert next(params for path, params in requests if path.endswith("/search/jql"))["jql"] == source["query"]
