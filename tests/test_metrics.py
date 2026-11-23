import copy
from datetime import timedelta

import pytest

from jira_flow.metrics import calculate, lead_comparison, working_seconds

from conftest import BASE, issue


def test_lead_ratios_and_type_groups(config):
    result = lead_comparison([1, 3, 20], 6)
    assert result["median_to_mean"] == .375
    assert result["median_to_overall"] == .5
    assert lead_comparison([0, 0], 0)["median_to_overall"] is None
    assert lead_comparison([0, 0], 0)["median_to_mean"] is None
    assert lead_comparison([], 5)["median_to_mean"] is None
    first = issue(config)
    second = issue(config, key="TEST-2", events=[(1, "2"), (4, "4")])
    second["issue_type"] = {"id": "2", "name": "Bug"}
    config["jira"]["issue_type_ids"].append("2")
    m = calculate([first, second], config)
    types = {r["name"]: r for r in m["lead"]["types"]}
    assert types["Task"]["p50"] == 12
    assert types["Bug"]["p50"] == 3
    assert types["Task"]["median_to_overall"] == 12/7.5
    assert types["Bug"]["median_to_overall"] == 3/7.5
    assert sum(t["n"] for t in types.values()) == m["lead"]["stats"]["n"]


def test_aging_strip_matches_table_visits_and_excludes_done_and_outside(config):
    tasks = [issue(config), issue(config, key="TEST-2", events=[(1, "2"), (3, "4")], created=0),
             issue(config, key="TEST-3", events=[(1, "2"), (2, "9"), (4, "3"), (5, "4")], created=0)]
    result = calculate(tasks, config)
    m = result["flow_breakdown"]
    segments = {s["key"]: s for s in m["segments"]}
    assert list(segments) == ["todo", "development", "review"]
    # The reopened task spent 4 days in Work and 5 in Review across all its visits.
    for key, expected in {"todo": 4/3, "development": 7/3, "review": 3}.items():
        assert segments[key]["mean"] == pytest.approx(expected)
        table_row = next(r for r in result["aging_columns"] if r["key"] == key)
        assert (segments[key]["mean"], segments[key]["n"]) == (table_row["mean"], table_row["n"])
    assert segments["development"]["n"] == 3 and segments["review"]["n"] == 2
    assert m["sum_means"] == pytest.approx(4/3+7/3+3)
    assert sum(s["fraction"] for s in m["segments"]) == pytest.approx(1)


def test_aging_strip_includes_stage_exits_without_full_path_and_preserves_missing(config):
    missing = issue(config, events=[(1, "4")])
    missing["status_history"][0].update({"from": "2", "from_label": "Work"})
    reopened = issue(config, key="TEST-2", events=[(1, "4"), (2, "2")])
    cutoff = issue(config, key="TEST-3", events=[(1, "2"), (21, "4")])
    result = calculate([missing, reopened, cutoff], config)["flow_breakdown"]
    assert result["sum_means"] == 4
    segments = {s["key"]: s for s in result["segments"]}
    assert segments["todo"]["n"] == 2 and segments["development"]["n"] == 1
    assert segments["review"]["mean"] is None and segments["review"]["fraction"] is None
    zero = issue(config, key="TEST-4", events=[(1, "2"), (1, "4")], created=1)
    result = calculate([zero], config)["flow_breakdown"]
    assert result["sum_means"] == 0
    assert all(s["fraction"] is None for s in result["segments"])
    assert [s["mean"] for s in result["segments"]] == [0, 0, None]


def test_status_time_order_follows_board_right_to_left_and_top_four(config):
    tasks = [issue(config)]
    tasks += [issue(config, key=f"TEST-{k+10}", events=[(-.5, "2"), (k, "3")]) for k in range(1, 7)]
    rows = calculate(tasks, config)["status_time"]
    assert [r["key"] for r in rows] == ["3", "5", "2", "1"]
    review = rows[0]
    assert len(review["active"]) == 6
    assert [r["key"] for r in review["top_tasks"]] == ["TEST-11", "TEST-12", "TEST-13", "TEST-14"]


def test_status_time_sums_only_visits_to_the_current_status(config):
    closed = issue(config)
    opened = issue(config, key="TEST-2", events=[(1, "2"), (2, "3"), (3, "2")])
    # Same-status event must not reset the open visit that began on day 3.
    opened["status_history"].append({"at": (BASE+timedelta(days=20)).isoformat(), "history_id": "100",
                                     "from": "2", "from_label": "2", "to": "2", "to_label": "renamed"})
    result = calculate([closed, opened], config, {"status_categories": {"4": "done", "1": "new"}})
    rows = {r["key"]: r for r in result["status_time"]}
    assert "4" not in rows
    # The closed task spent one day in status 2 on each of three visits.
    assert rows["2"]["n"] == 1 and rows["2"]["mean"] == 3
    assert rows["2"]["active"][0]["key"] == "TEST-2"
    assert rows["2"]["active"][0]["days"] == 19
    assert rows["2"]["active"][0]["current_visit_days"] == 18
    assert rows["2"]["active"][0]["summary"] == "Fixture issue"
    assert rows["3"]["n"] == 2 and rows["3"]["mean"] == 3
    rejected = issue(config, key="TEST-3", events=[(1, "9")])
    terminal_rows = calculate([rejected], config, {"status_categories": {"9": "done"}})["status_time"]
    assert "9" not in {r["key"] for r in terminal_rows}
    no_baseline = calculate([issue(config, events=[(1, "2")])], config)["status_time"]
    development = next(r for r in no_baseline if r["key"] == "2")
    assert development["mean"] is None
    assert [t["key"] for t in development["top_tasks"]] == ["TEST-1"]
    assert development["active"][0]["days"] == 20


def test_batch_status_entries_keep_seconds_and_offsets_before_display_rounding(config):
    first = issue(config, key="TEST-2", events=[(1, "2"), (22, "4")])
    second = issue(config, key="TEST-3", events=[(1, "2"), (22, "4")])
    first["status_history"][0].update({"at": "2026-09-02T10:23:31.270+04:00", "from": "", "from_label": "TO DO"})
    second["status_history"][0].update({"at": "2026-09-02T09:23:33.854+03:00", "from": "", "from_label": "TO DO"})
    result = calculate([issue(config), first, second], config)
    development = next(r for r in result["status_time"] if r["key"] == "2")
    active = {r["key"]: r for r in development["active"]}
    a, b = active["TEST-2"], active["TEST-3"]
    # Sep 2, 09:23:31.270 MSK to Sep 22, 00:00 = 20 days minus 33,811.270 seconds.
    assert a["days"] == pytest.approx((20*86400-33811.270)/86400, abs=1e-10, rel=0)
    assert (a["days"]-b["days"])*86400 == pytest.approx(2.584, abs=1e-8)
    assert a["days"] != b["days"] and round(a["days"], 3) == round(b["days"], 3)
    assert a["entered_at"] == "2026-09-02T10:23:31.270000+04:00"
    assert development["n"] == 1 and development["mean"] == 3
    assert {r["key"] for r in development["top_tasks"]} == {"TEST-2", "TEST-3"}


def test_open_age_keeps_first_entry_before_reporting_window(config):
    opened = issue(config, key="TEST-2", events=[(-40, "2")], created=-50)
    result = calculate([issue(config), opened], config)
    development = next(r for r in result["status_time"] if r["key"] == "2")
    assert development["active"][0]["days"] == 61  # Day -40 to cutoff day 21; no clipping to 21 days.
    assert development["n"] == 1 and development["mean"] == 3


def test_updated_count_uses_all_change_timestamps_and_period_boundaries(config):
    old = issue(config, key="TEST-1", events=[], created=-10)
    old["change_timestamps"] = [(BASE-timedelta(days=1)).isoformat()]
    changed = issue(config, key="TEST-2", events=[], created=-10)
    changed["change_timestamps"] = [BASE.isoformat(), (BASE+timedelta(days=21)).isoformat()]
    cutoff_only = copy.deepcopy(old)
    cutoff_only["key"] = "TEST-3"
    cutoff_only["change_timestamps"] = [(BASE+timedelta(days=21)).isoformat()]
    result = calculate([old, changed, cutoff_only], config)
    assert result["updated_in_period"]["keys"] == ["TEST-2"]
    assert result["updated_in_period"]["incomplete_evidence"] == []


def test_reopening_same_column_and_estimate_before_start(config):
    result = calculate([issue(config)], config)
    assert result["people"] == [{"id": "A", "name": "Owner A"}]
    assert result["throughput"]["unique"] == 1
    assert result["throughput"]["events"] == 2
    assert result["throughput"]["weekly_sum"] == 2
    assert result["throughput"]["full_week_stats"]["mean"] == 1
    assert result["lead"]["stats"]["p50"] == 12
    assert next(r for r in result["aging_columns"] if r["key"] == "development")["mean"] == 4
    assert next(r for r in result["aging_statuses"] if r["key"] == "2")["mean"] == 3
    accuracy = result["accuracy"]
    assert accuracy["tasks"][0]["estimate"] == "S"  # Later M must not rewrite the baseline.
    assert accuracy["tasks"][0]["scope_days"] == 9  # Done gap excluded; all returns included.
    assert accuracy["tasks"][0]["actual_to_estimate"] is None  # Ordinal sizes have no time ratio.
    assert accuracy["bubbles"][0]["left"] == 12
    assert accuracy["bubbles"][0]["y"] == 1


def test_completion_counts_are_distinct_from_lead_samples_and_group_by_id(config):
    config["assignee_ids"] = []
    valid = issue(config)  # Reclosed in a later week: two events, one distinct issue.
    missing_start = issue(config, key="TEST-2", events=[(3, "4")])
    rejected = issue(config, key="TEST-3", events=[(1, "2"), (4, "6")])
    assignee = config["fields"]["assignee_id"]
    for task, person in ((valid, "A"), (missing_start, "B"), (rejected, "A")):
        task["field_values"][assignee] = {"accountId": person, "displayName": "Same name"}
        task["field_history"][assignee] = []
    result = calculate([valid, missing_start, rejected], config, {"status_categories": {"6": "done"}})
    assert result["throughput"]["unique"] == 2
    assert result["throughput"]["events"] == 3
    assert result["lead"]["stats"]["n"] == 1
    completed = {row["id"]: row["unique"] for row in result["throughput"]["groups"]["person"]}
    sampled = {row["id"]: row["n"] for row in result["lead"]["people"]}
    assert completed == {"A": 1, "B": 1}
    assert sampled == {"A": 1, "B": 0}
    assert result["lead"]["cohort"] == {
        "throughput_n": 2, "matched_n": 1, "missing_n": 1, "lead_only_n": 0,
        "coverage": .5, "exclusion_counts": {"missing_start": 1}, "legacy_before_start_n": 0, "historical_start_n": 0,
    }
    cohorts = {row["id"]: row["cohort"] for row in result["lead"]["people"]}
    assert cohorts["A"]["coverage"] == 1
    assert cohorts["B"]["coverage"] == 0
    assert cohorts["B"]["exclusion_counts"] == {"missing_start": 1}
    assert {row["name"] for row in result["lead"]["people"]} == {"Same name"}
    for dimension, lead_dimension in (("type", "types"), ("priority", "priorities")):
        assert sum(row["unique"] for row in result["throughput"]["groups"][dimension]) == 2
        assert sum(row["n"] for row in result["lead"][lead_dimension]) == 1
        assert {row["id"] for row in result["lead"][lead_dimension]} == {
            row["id"] for row in result["throughput"]["groups"][dimension]}
        for counter in ("throughput_n", "matched_n", "missing_n", "lead_only_n"):
            assert sum(row["cohort"][counter] for row in result["lead"][lead_dimension]) == result["lead"]["cohort"][counter]


def test_lead_cohort_can_include_issues_outside_throughput(config):
    config["metrics"]["lead_time"]["end"] = {"type": "status", "id": "3"}
    task = issue(config, events=[(1, "2"), (4, "3")])
    result = calculate([task], config)
    assert result["throughput"]["unique"] == 0
    assert result["throughput"]["groups"]["person"][0]["unique"] == 0
    assert result["lead"]["people"][0]["n"] == 1
    assert result["lead"]["people"][0]["mean"] == 3
    assert result["lead"]["cohort"] == {
        "throughput_n": 0, "matched_n": 0, "missing_n": 0, "lead_only_n": 1,
        "coverage": None, "exclusion_counts": {}, "legacy_before_start_n": 0, "historical_start_n": 0,
    }


def test_lead_coverage_explains_reopened_issues_and_empty_samples(config):
    reopened = issue(config, events=[(1, "2"), (3, "4"), (4, "2")])
    no_start = issue(config, key="TEST-2", events=[(2, "4")])
    result = calculate([reopened, no_start], config)
    assert result["throughput"]["unique"] == 2
    assert result["lead"]["stats"]["n"] == 0
    assert result["lead"]["cohort"] == {
        "throughput_n": 2, "matched_n": 0, "missing_n": 2, "lead_only_n": 0,
        "coverage": 0, "exclusion_counts": {"reopened_unfinished": 1, "missing_start": 1}, "legacy_before_start_n": 0, "historical_start_n": 0,
    }
    assert result["lead"]["people"][0]["mean"] is None
    empty = calculate([], config)
    assert empty["lead"]["cohort"]["coverage"] is None
    assert empty["lead"]["cohort"]["exclusion_counts"] == {}


def test_exclusive_cutoff_and_inclusive_start(config):
    at_start = issue(config, key="TEST-1", events=[(-2, "2"), (0, "4")], created=-3)
    at_end = issue(config, key="TEST-2", events=[(0, "2"), (21, "4")])
    result = calculate([at_start, at_end], config)
    assert result["throughput"]["unique"] == 1
    assert result["lead"]["stats"]["n"] == 1
    assert result["lead"]["stats"]["mean"] == 2
    assert next(t for t in result["tasks"] if t["key"] == "TEST-2")["status"] == "2"
    assert result["cfd_columns"]["points"][-1]["counts"] == [0, 1, 0, 1, 0]


def test_legacy_development_is_not_an_exact_status_alias(config):
    task = issue(config, events=[(1, "3"), (3, "4")])
    task["status_history"][0]["from"] = ""
    task["status_history"][0]["from_label"] = "DEVELOPMENT"
    result = calculate([task], config)
    assert result["throughput"]["unique"] == 1
    assert result["lead"]["stats"]["n"] == 0
    assert result["lead"]["exclusions"][0]["reason"] == "missing_start"
    assert "legacy:DEVELOPMENT" in result["cfd_statuses"]["keys"]


def test_legacy_history_before_configured_start_is_visible_without_changing_duration(config):
    task = issue(config, events=[(1, "2"), (3, "4")])
    task["status_history"][0].update({"from": "", "from_label": "Old work"})
    result = calculate([task], config)
    assert result["lead"]["tasks"][0]["legacy_before_start"] is True
    assert result["lead"]["stats"]["mean"] == 2  # The explicit status boundary stays unchanged.
    assert result["lead"]["cohort"]["legacy_before_start_n"] == 1
    for group in ("people", "types", "priorities"):
        assert result["lead"][group][0]["cohort"]["legacy_before_start_n"] == 1


def test_multiple_legacy_variants_are_one_stage_for_all_time_metrics(config):
    config["columns"]["development"]["statuses"][0]["legacy"] = [
        {"id": "9", "name": "Old work"}, {"name": "Older work"}]
    task = issue(config, events=[(1, "9"), (3, "8"), (5, "2"), (7, "3"), (8, "4")])
    task["status_history"][0]["to_label"] = "Old work"
    task["status_history"][1].update({"from_label": "Old work", "to": "", "to_label": "Older work"})
    task["status_history"][2].update({"from": "", "from_label": "Older work"})
    original = copy.deepcopy(task["status_history"])
    result = calculate([task], config)
    lead = result["lead"]["tasks"][0]
    assert lead["days"] == 7  # First old entry on day 1 to completion on day 8.
    assert lead["start_source"]["source_id"] == "9"
    assert lead["start_source"]["matched_by"] == "id"
    assert result["lead"]["cohort"]["historical_start_n"] == 1
    assert next(r for r in result["aging_statuses"] if r["key"] == "2")["mean"] == 6
    assert next(r for r in result["aging_columns"] if r["key"] == "development")["mean"] == 6
    assert result["accuracy"]["tasks"][0]["estimate"] == "S"  # Frozen on day 1, before the day-4 edit.
    assert result["accuracy"]["tasks"][0]["scope_days"] == 7
    assert result["throughput"]["unique"] == 1
    assert "9" not in result["cfd_statuses"]["keys"]
    assert "legacy:Older work" not in result["cfd_statuses"]["keys"]
    dev_index = result["cfd_columns"]["keys"].index("development")
    after_first_entry = next(p for p in result["cfd_columns"]["points"] if p["at"].startswith("2026-09-02"))
    assert after_first_entry["counts"][dev_index] == 1
    assert task["status_history"] == original
    assert any(r["matched_by"] == "name" for r in task["resolved_status_history"])


def test_name_only_legacy_match_applies_only_to_missing_source_ids(config):
    config["columns"]["development"]["statuses"][0]["legacy"] = [{"name": "Old work"}]
    blank = issue(config, events=[(1, "9"), (4, "4")])
    blank["status_history"][0].update({"to": "", "to_label": "Old work"})
    blank["status_history"][1].update({"from": "", "from_label": "Old work"})
    present = copy.deepcopy(blank)
    present["key"] = "TEST-2"
    present["status_history"][0]["to"] = "9"
    present["status_history"][1]["from"] = "9"
    result = calculate([blank, present], config)
    assert result["throughput"]["unique"] == 2
    assert [r["key"] for r in result["lead"]["tasks"]] == ["TEST-1"]
    assert result["lead"]["tasks"][0]["days"] == 3
    assert result["lead"]["tasks"][0]["start_source"]["matched_by"] == "name"
    assert result["lead"]["cohort"]["missing_n"] == 1


def test_legacy_start_before_period_survives_reopenings_and_exclusive_cutoff(config):
    config["columns"]["development"]["statuses"][0]["legacy"] = [{"id": "9"}]
    task = issue(config, events=[(-2, "9"), (2, "4"), (5, "9"), (9, "4"), (21, "9")], created=-3)
    result = calculate([task], config)
    assert result["lead"]["tasks"][0]["days"] == 11
    assert result["lead"]["tasks"][0]["start_source"]["source_id"] == "9"
    assert result["throughput"]["events"] == 2
    assert result["throughput"]["unique"] == 1
    assert result["tasks"][0]["status"] == "4"  # Reopening exactly at the cutoff is not included.
    assert next(r for r in result["aging_statuses"] if r["key"] == "2")["mean"] == 8
    assert all(not r["at"].startswith("2026-09-22") for r in task["resolved_status_history"])


def test_name_only_legacy_can_resolve_a_known_id_from_the_project_catalog(config):
    config["columns"]["development"]["statuses"][0]["legacy"] = [{"name": "Old work"}]
    task = issue(config, events=[(1, "9"), (4, "4")])
    task["status_history"][0]["to_label"] = "Old work"
    task["status_history"][1]["from_label"] = "Old work"
    catalog = [{"id": "9", "name": "Old work"}]
    result = calculate([task], config, {"status_catalog": catalog})
    assert result["lead"]["stats"]["mean"] == 3
    assert result["lead"]["tasks"][0]["start_source"]["matched_by"] == "id"
    with pytest.raises(ValueError, match="Ambiguous legacy name"):
        calculate([task], config, {"status_catalog": catalog+[{"id": "8", "name": "Old work"}]})
    scoped = [{"id": "9", "name": "Old work", "scope": {"type": "PROJECT", "project": {"id": "100"}}},
              {"id": "8", "name": "Old work", "scope": {"type": "GLOBAL"}}]
    assert calculate([task], config, {"status_catalog": scoped})["lead"]["stats"]["mean"] == 3


def test_histories_without_id_preserve_rename_uncertainty(config):
    task = issue(config, events=[(1, "3"), (3, "4")])
    task["status_history"][0].update({"from": "", "from_label": "Old A", "to": "", "to_label": "Old B"})
    task["status_history"][1].update({"from": "", "from_label": "Renamed B"})
    result = calculate([task], config)
    assert len(result["warnings"]) == 1
    cfd = result["cfd_statuses"]
    index = cfd["keys"].index("legacy:__unconfirmed__")
    assert cfd["means"][index] == pytest.approx(2/21)


def test_missing_multiple_estimates_and_bubble_frequency(config):
    tasks = [issue(config, key=f"TEST-{i}") for i in range(1, 5)]
    field = config["fields"]["estimate_id"]
    tasks[2]["field_history"][field] = []
    tasks[2]["field_values"][field] = None
    tasks[3]["field_history"][field] = []
    tasks[3]["field_values"][field] = [{"id": "10"}, {"id": "20"}]
    result = calculate(tasks, config)["accuracy"]
    assert result["n"] == 2 and result["lead_n"] == 4
    assert result["bubbles"][0]["n"] == 2
    assert result["exclusion_counts"] == {"missing_before_start": 1, "multiple_estimates": 1}


def test_temporal_estimates_and_sum_of_visits(config):
    changed = copy.deepcopy(config)
    changed["metrics"]["estimate_accuracy"].update({"unit": "hours", "options": []})
    task = issue(config)
    field = config["fields"]["estimate_id"]
    task["field_values"][field], task["field_history"][field] = 72, []
    result = calculate([task], changed)["accuracy"]
    assert result["tasks"][0]["actual_to_estimate"] == 3  # 9 calendar days / 72 hours.
    assert result["groups"][0]["over_estimate"] == 1
    assert result["bubbles"][0]["y"] == 72


def test_working_time_excludes_weekends(config):
    from zoneinfo import ZoneInfo
    settings = config["metrics"]["estimate_accuracy"]["working_calendar"]
    # Friday 09:00 to Monday 17:00 in the configured timezone = two eight-hour workdays.
    friday = BASE+timedelta(days=3, hours=9)
    monday = BASE+timedelta(days=6, hours=17)
    assert working_seconds(friday, monday, settings, ZoneInfo("Europe/Moscow")) == 16*3600


def test_empty_cohort_and_initial_done_reopening(config):
    empty = calculate([], config)
    assert empty["cohort_size"] == 0 and empty["lead"]["stats"]["mean"] is None
    task = issue(config, events=[(2, "2")])
    task["status_history"][0].update({"from": "4", "from_label": "Done"})
    result = calculate([task], config)
    cfd = result["cfd_columns"]
    index = cfd["keys"].index("done")
    assert cfd["points"][0]["counts"][index] == 1
    assert cfd["points"][-1]["counts"][index] == 0  # Plotted net change must be -1, not clamped.


def test_completed_span_and_sum_visits_are_distinct(config):
    config["metrics"]["aging"]["method"] = "sum_visits"
    result = calculate([issue(config)], config)
    assert next(r for r in result["aging_columns"] if r["key"] == "development")["mean"] == 4
    assert next(r for r in result["aging_statuses"] if r["key"] == "2")["mean"] == 3


def test_last_start_uses_first_subsequent_completion(config):
    config["metrics"]["lead_time"].update({"start_event": "last_entry", "end_event": "first_entry"})
    result = calculate([issue(config)], config)
    assert result["lead"]["stats"]["n"] == 1
    assert result["lead"]["stats"]["mean"] == 4  # Restart day 9, next completion day 13.


def test_broken_estimate_chain_is_not_silently_accepted(config):
    task = issue(config)
    task["field_history"][config["fields"]["estimate_id"]][0]["to"] = "[999]"
    with pytest.raises(ValueError, match="Broken field history"):
        calculate([task], config)


def test_field_cutoff_and_old_option_id_remapping(config):
    task = issue(config)
    fields = config["fields"]
    task["field_history"][fields["assignee_id"]][0]["at"] = (BASE+timedelta(days=21)).isoformat()
    task["field_history"][fields["estimate_id"]][0]["at"] = (BASE+timedelta(days=1)).isoformat()
    # A pre-cutoff import remapped an option ID without a later field-change event.
    task["field_history"][fields["priority_id"]][0].update({"at": (BASE-timedelta(days=20)).isoformat(), "to": "old-id"})
    result = calculate([task], config)
    assert result["people"][0]["id"] == "A"  # Change exactly at end is rolled back.
    assert result["accuracy"]["tasks"][0]["estimate"] == "S"  # Estimate exactly at start is not baseline.
    assert result["tasks"][0]["priority_id"] == "p2"  # Do not audit irrelevant history before cutoff.


def test_working_lead_and_aging_count_only_weekdays_and_keep_calendar_estimates(config):
    config["metrics"]["lead_time"]["time_basis"] = "working"
    config["metrics"]["aging"]["time_basis"] = "working"
    # September 4 is Friday; September 7 is Monday. A Saturday-only delivery remains included.
    friday = issue(config, events=[(3, "2"), (6, "4")])
    saturday = issue(config, key="TEST-2", events=[(4.25, "2"), (4.5, "4")])
    open_task = issue(config, key="TEST-3", events=[(3, "2")])
    result = calculate([friday, saturday, open_task], config)
    assert result["throughput"]["unique"] == 2
    assert {t["key"]: t["days"] for t in result["lead"]["tasks"]} == {"TEST-1": 1, "TEST-2": 0}
    stage = next(r for r in result["status_time"] if r["key"] == "2")
    assert (stage["n"], stage["mean"], stage["active"][0]["days"]) == (2, .5, 12)
    assert next(t for t in result["accuracy"]["tasks"] if t["key"] == "TEST-1")["scope_days"] == 3


def test_type_start_uses_first_allowed_status_and_common_fallback(config):
    config["jira"]["issue_type_ids"] += ["10"]
    config["metrics"]["lead_time"]["start_by_type"] = {"10": ["3", "5"]}
    first = issue(config, events=[(1, "2"), (2, "5"), (3, "3"), (6, "4")])
    second = issue(config, key="TEST-2", events=[(1, "2"), (2, "5"), (3, "3"), (6, "4")])
    second["issue_type"] = {"id": "10", "name": "Bug"}
    result = calculate([first, second], config)
    assert result["throughput"]["unique"] == 2
    assert {t["key"]: (t["days"], t["start_source"]["status_id"]) for t in result["lead"]["tasks"]} == {
        "TEST-1": (5, "2"), "TEST-2": (4, "5")}


def test_merge_accounts_after_cutoff_and_count_each_issue_once(config):
    config["people_merge"] = [{"ids": ["A", "A2"], "name": "Unified owner"}]
    config["assignee_ids"] = ["A2"]
    original, alias, outsider = (issue(config, key=f"TEST-{n}") for n in range(1, 4))
    alias["field_history"]["assignee"][0]["from"] = "A2"
    outsider["field_history"]["assignee"] = []
    result = calculate([original, alias, outsider], config)
    assert result["people"] == [{"id": "A", "name": "Unified owner"}]
    assert result["throughput"]["unique"] == 2
    assert result["lead"]["people"][0]["n"] == 2
    assert result["lead"]["tasks"][0]["person_id"] == "A"
