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
