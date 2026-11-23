import copy
import json
from pathlib import Path

import pytest
import yaml

from jira_flow.cli import main
from jira_flow.config import load_config, parse_overrides, validate
from jira_flow.report import build_artifacts, run_report

from conftest import issue, snapshot


def test_yaml_and_parameter_only_configuration_are_equivalent(config, tmp_path):
    path = tmp_path/"team.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    direct = load_config(None, config)
    from_yaml = load_config(path)
    assert direct == from_yaml
    assert parse_overrides(["metrics.lead_time.rolling_weeks=6", "assignee_ids=[]"])["metrics.lead_time.rolling_weeks"] == 6
    with pytest.raises(ValueError, match="Unknown"):
        load_config(path, {"period.typo": "2026-01-01"})


def test_invalid_mapping_and_naive_timestamps_fail(config):
    bad = copy.deepcopy(config)
    bad["columns"]["review"]["statuses"] = [{"id": "2", "name": "Duplicate"}]
    with pytest.raises(ValueError, match="multiple columns"):
        validate(bad)
    bad = copy.deepcopy(config)
    bad["period"]["start"] = "2026-09-01T00:00:00"
    with pytest.raises(ValueError, match="offset"):
        validate(bad)


def test_named_status_schema_and_legacy_conflicts(config):
    config["columns"]["development"]["statuses"][0]["legacy"] = [
        {"id": "9", "name": "Old work"}, {"name": "Older work"}]
    validate(config)
    conflicting = copy.deepcopy(config)
    conflicting["columns"]["review"]["statuses"][0]["legacy"] = [{"name": "Old work"}]
    with pytest.raises(ValueError, match="Ambiguous legacy"):
        validate(conflicting)
    conflicting = copy.deepcopy(config)
    conflicting["columns"]["review"]["statuses"][0]["legacy"] = [{"id": "2"}]
    with pytest.raises(ValueError, match="current status"):
        validate(conflicting)
    conflicting = copy.deepcopy(config)
    conflicting["columns"]["review"]["statuses"][0]["legacy"] = [{}]
    with pytest.raises(ValueError, match="Legacy statuses"):
        validate(conflicting)


def test_legacy_column_format_is_upgraded_without_changing_status_ids(config):
    old = {key: {"label": col["name"], "status_ids": [s["id"] for s in col["statuses"]]}
           for key, col in config["columns"].items()}
    upgraded = load_config(None, {**config, "columns": old})
    assert upgraded["columns"]["development"] == {
        "name": "Work", "statuses": [{"id": "2", "name": "2"}, {"id": "5", "name": "5"}]}


def test_exact_five_files_and_offline_cli_replay(config, tmp_path, capsys):
    source = snapshot(config, [issue(config)])
    task = source["issues"][0]
    task["change_timestamps"] = [h["at"] for h in task["status_history"]]
    source["hierarchy"] = {"verified_project_count": 2, "link_types": [], "nodes": [
        {"id": str(i), "key": f"TEST-{i}", "created_at": task["created_at"],
         "parent_id": "1" if i == 2 else None, "subtask_ids": ["2"] if i == 1 else [],
         "links": [], "changes": []} for i in (1, 2)]}
    directory = build_artifacts(source, config)
    names = {p.name for p in directory.iterdir()}
    assert len(names) == 5
    assert {"issues.csv", "issues.json", "metrics.json", "report.html"} <= names
    assert sum(n.startswith("config_TEST_") and n.endswith(".yaml") for n in names) == 1
    exported = json.loads((directory/"issues.json").read_text(encoding="utf-8"))
    assert len(exported["issues"][0]["status_history"]) == 9
    assert exported["issues"][0]["closed_at"].startswith("2026-09-14")
    html = (directory/"report.html").read_text(encoding="utf-8")
    assert '<section id="observations"' not in html
    assert '<section id="accuracy"' in html and 'id="estimate-bubbles"' in html
    assert 'connect-src \'none\'' in html and '<script src=' not in html
    cfg_file = tmp_path/"team.yaml"
    cfg_file.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert main(["build", "--config", str(cfg_file), "--input", str(directory/"issues.json")]) == 0
    replay = json.loads(capsys.readouterr().out.strip())
    first = json.loads((directory/"metrics.json").read_text(encoding="utf-8"))
    assert first["decomposition"]["stats"]["mean_children"] == 1
    assert '<h2>5. Глубина декомпозиции</h2>' in html
    assert '<h2>6. Время в статусе</h2>' in html and '<h2>7. Точность оценок</h2>' in html
    assert 'decomposition-closed' not in html and '5.3. Закрытые' not in html
    second = json.loads((Path(replay["output"])/"metrics.json").read_text(encoding="utf-8"))
    assert first == second
    arguments = ["build", "--input", str(directory/"issues.json")]
    for key, value in config.items():
        arguments.extend(["--set", key+"="+json.dumps(value)])
    assert main(arguments) == 0
    parameter_only = json.loads(capsys.readouterr().out.strip())
    third = json.loads((Path(parameter_only["output"])/"metrics.json").read_text(encoding="utf-8"))
    assert first == third
    with pytest.raises(ValueError, match="cutoff"):
        run_report(cfg_file, {"period.end": "2026-09-23T00:00:00+03:00"}, directory/"issues.json")



def test_time_basis_type_start_and_account_groups_in_config(config):
    config["metrics"]["lead_time"].pop("time_basis")
    config["metrics"]["aging"] = {"method": "first_entry_to_last_exit"}
    path = Path(config["output_root"]).parent/"team.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    prepared = load_config(path, {"metrics.lead_time.start_by_type.1": ["5", "3"]})
    assert prepared["metrics"]["lead_time"]["time_basis"] == "working"
    assert prepared["metrics"]["lead_time"]["start_by_type"] == {"1": ["5", "3"]}
    assert prepared["metrics"]["aging"] == {"method": "sum_visits", "time_basis": "working"}
    with pytest.raises(ValueError, match="configured issue type IDs"):
        load_config(path, {"metrics.lead_time.start_by_type.99": ["5"]})
    with pytest.raises(ValueError, match="non-overlapping"):
        load_config(path, {"people_merge": [{"ids": ["A", "A2"], "name": "One"},
                                            {"ids": ["A2", "A3"], "name": "Two"}]})


def test_offline_scope_requires_all_accounts_in_selected_alias_groups(config):
    source = snapshot(config, [issue(config)])
    config["people_merge"] = [{"ids": ["A", "A2"], "name": "Unified owner"}]
    with pytest.raises(ValueError, match="requested assignees"):
        build_artifacts(source, config)
    source["metadata"]["coverage"]["assignee_ids"] = ["A", "A2"]
    output = build_artifacts(source, config)
    issues = json.loads((output/"issues.json").read_text(encoding="utf-8"))
    assert issues["metadata"]["coverage"]["assignee_ids"] == ["A", "A2"]
    assert issues["issues"][0]["assignee_at_period_end"] == {"id": "A", "name": "Unified owner"}
