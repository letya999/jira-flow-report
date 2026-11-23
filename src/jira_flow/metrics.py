"""Pure calculations from exported issue histories and a validated configuration."""

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .config import assignee_aliases, instant, selected_assignee_ids, status_aliases, status_definitions

UTC = timezone.utc
OUTSIDE = "__outside__"


def seconds(left, right):
    return (right.astimezone(UTC) - left.astimezone(UTC)).total_seconds()


def days(left, right):
    return seconds(left, right) / 86400


def duration_days(left, right, basis, tz):
    if basis == "calendar":
        return days(left, right)
    day = left.astimezone(tz).date()
    stop = right.astimezone(tz).date()
    left, right = left.astimezone(UTC), right.astimezone(UTC)
    total = 0.0
    while day <= stop:
        tomorrow = day + timedelta(days=1)
        if day.weekday() < 5:
            begin = datetime.combine(day, time.min, tz).astimezone(UTC)
            end = datetime.combine(tomorrow, time.min, tz).astimezone(UTC)
            total += max(0, (min(right, end) - max(left, begin)).total_seconds())
        day = tomorrow
    return total / 86400


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(position-lo)


def stats(values):
    return {"n": len(values), "p50": percentile(values, .5), "p85": percentile(values, .85),
            "mean": statistics.mean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "range": max(values)-min(values) if values else None}


def lead_comparison(values, overall_median):
    result = stats(values)
    median, mean = result["p50"], result["mean"]
    result["median_to_mean"] = median/mean if median is not None and mean else None
    result["median_to_overall"] = median/overall_median if median is not None and overall_median else None
    return result


def _history_value(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [{"id": str(v), "value": label} for v in parsed]
        except json.JSONDecodeError:
            pass
    return {"id": str(value), "value": label}


def _value_keys(value):
    if value is None or value == "":
        return ()
    if isinstance(value, list):
        return tuple(sorted(k for item in value for k in _value_keys(item)))
    if isinstance(value, dict):
        value = value.get("accountId", value.get("id"))
    text = str(value)
    try:
        text = str(Decimal(text).normalize())
    except InvalidOperation:
        pass
    return (text,)


def value_before(issue, field_id, cutoff):
    """Exclusive cutoff: undo changes at or after the requested moment."""
    value = issue["field_values"].get(field_id)
    for event in reversed(issue["field_history"].get(field_id, [])):
        if instant(event["at"]) < cutoff:
            break
        target = _history_value(event["to"], event["to_label"])
        if _value_keys(value) != _value_keys(target):
            raise ValueError("Broken field history: " + issue["key"] + " / " + field_id)
        value = _history_value(event["from"], event["from_label"])
    # Older option IDs can have been remapped by Jira migrations without field-change events.
    # Validate the chain being rolled back, not unrelated history before the requested moment.
    return value


def identity(value, missing="__missing__"):
    if value is None:
        return missing, "Не указан (пусто)"
    if isinstance(value, list):
        if len(value) != 1:
            return "__multiple__", "Несколько значений"
        value = value[0]
    if isinstance(value, dict):
        key = value.get("accountId", value.get("id", ""))
        label = value.get("displayName") or value.get("value") or value.get("name") or str(key)
        return str(key), str(label)
    return str(value), str(value)


def status_key(status_id, name):
    if status_id:
        return str(status_id)
    if not name:
        raise ValueError("Historical status has neither ID nor name")
    return "legacy:" + name.strip()


def timeline(issue, end, aliases=None, labels=None):
    aliases, labels = aliases or {}, labels or {}
    events = issue["status_history"]
    created = instant(issue["created_at"])
    current = issue["current_status"]
    first_id, first_name = (events[0]["from"], events[0]["from_label"]) if events else (current["id"], current["name"])
    raw_initial = status_key(first_id, first_name)
    initial = aliases.get(raw_initial, raw_initial)
    current_key = status_key(current["id"], current["name"])
    current_key = aliases.get(current_key, current_key)
    names = {current_key: labels.get(current_key, current["name"])}
    state, cursor = initial, created
    raw_state = raw_initial
    visits, changes, warnings, uncertain = [], [], [], set()
    resolutions, legacy_instants = [], [created] if raw_initial.startswith("legacy:") else []

    def source(at, raw_id, name, target, history_id=None):
        raw = status_key(raw_id, name)
        return {"at": at.isoformat(), "status_id": target, "source_id": raw_id or None, "source_name": name,
                "matched_by": ("name" if raw.startswith("legacy:") else "id") if raw in aliases else None,
                "history_id": history_id}

    entries = [source(created, first_id, first_name, initial)]
    for event in events:
        when = instant(event["at"])
        if when >= end:
            break
        raw_before = status_key(event["from"], event["from_label"])
        raw_after = status_key(event["to"], event["to_label"])
        before, after = aliases.get(raw_before, raw_before), aliases.get(raw_after, raw_after)
        for key, label in ((before, event["from_label"]), (after, event["to_label"])):
            names[key] = labels.get(key, (label or key) + (" (старый)" if key.startswith("legacy:") else ""))
        for side, raw, target in (("from", raw_before, before), ("to", raw_after, after)):
            if raw.startswith("legacy:"):
                legacy_instants.append(when)
            if raw in aliases:
                resolutions.append({**source(when, event[side], event[side+"_label"], target, event["history_id"]), "side": side})
        if when < cursor:
            raise ValueError("Non-monotonic status history: " + issue["key"])
        if before != state:
            if not (raw_before.startswith("legacy:") and raw_state.startswith("legacy:")):
                raise ValueError("Broken status chain: " + issue["key"])
            unknown = "legacy:__unconfirmed__"
            names[unknown] = "Исторический статус не подтверждён"
            warnings.append({"key": issue["key"], "reason": "legacy_name_gap", "start": cursor.isoformat(), "end": when.isoformat()})
            uncertain.update((before, state, unknown))
            changes.append((cursor, state, unknown))
            state = unknown
        if state != after:
            if cursor < when:
                visits.append((cursor, when, state))
            changes.append((when, state, after))
            entries.append(source(when, event["to"], event["to_label"], after, event["history_id"]))
            state, cursor = after, when
        raw_state = raw_after
    if cursor < end:
        visits.append((cursor, end, state))
    names.setdefault(initial, labels.get(initial, first_name or initial))
    return {"initial": initial, "current": state, "visits": visits, "changes": changes,
            "names": names, "warnings": warnings, "uncertain": uncertain,
            "entries": entries, "resolutions": resolutions, "legacy_instants": legacy_instants}


def entries_for(t, created, match):
    entries = [created] if match(t["initial"]) else []
    exits = []
    for when, before, after in t["changes"]:
        was, now = match(before), match(after)
        if not was and now:
            entries.append(when)
        if was and not now:
            exits.append(when)
    return entries, exits


def boundary_match(boundary, mapping):
    if boundary["type"] == "status":
        return lambda s: s == boundary["id"]
    return lambda s: mapping.get(s, OUTSIDE) == boundary["id"]


def occupancy(all_visits, keys, start, end):
    indexes = {key: i for i, key in enumerate(keys)}
    deltas = defaultdict(Counter)
    deltas[start]
    for visits in all_visits:
        for left, right, state in visits:
            left, right = max(start, left), min(end, right)
            if left >= right:
                continue
            deltas[left][indexes[state]] += 1
            if right < end:
                deltas[right][indexes[state]] -= 1
    points, counts = [], [0]*len(keys)
    for at, delta in sorted(deltas.items()):
        old = list(counts)
        for i, amount in delta.items():
            counts[i] += amount
        if min(counts, default=0) < 0:
            raise ValueError("Negative CFD population")
        if at == start or old != counts:
            points.append({"at": at.isoformat(), "counts": list(counts)})
    points.append({"at": end.isoformat(), "counts": list(counts)})
    means = [0.]*len(keys)
    for a, b in zip(points, points[1:]):
        weight = seconds(instant(a["at"]), instant(b["at"])) / seconds(start, end)
        for i, amount in enumerate(a["counts"]):
            means[i] += amount*weight
    return points, means


def working_seconds(left, right, settings, tz):
    result = 0.
    day = left.astimezone(tz).date()
    stop = right.astimezone(tz).date()
    holidays = set(settings["holidays"])
    while day <= stop:
        if day.weekday() in settings["weekdays"] and day.isoformat() not in holidays:
            a = datetime.combine(day, time.fromisoformat(settings["start"]), tz)
            b = datetime.combine(day, time.fromisoformat(settings["end"]), tz)
            a, b = max(left, a), min(right, b)
            if a < b:
                result += seconds(a, b)
        day += timedelta(days=1)
    return result


def estimate_value(raw, config):
    if raw is None:
        return None, "missing_before_start"
    key, label = identity(raw)
    if key == "__multiple__":
        return None, "multiple_estimates"
    options = config["options"]
    if options:
        found = next(((i, o) for i, o in enumerate(options) if o["id"] == key), None)
        if found is None:
            return None, "unmapped_estimate_option"
        rank, option = found
        value = rank+1 if config["unit"] == "ordinal" else option["value"]
        return {"id": key, "label": option["label"], "value": value,
                "open_ended": option.get("open_ended", False)}, None
    if isinstance(raw, dict):
        raw = raw.get("id")
    if isinstance(raw, bool):
        return None, "invalid_estimate"
    try:
        number = float(raw)
    except (ValueError, TypeError):
        return None, "invalid_estimate"
    if not math.isfinite(number) or number < 0:
        return None, "invalid_estimate"
    return {"id": str(number), "label": str(number), "value": number, "open_ended": False}, None


def calculate(issues, config, metadata=None):
    metadata = metadata or {}
    start, end = instant(config["period"]["start"]), instant(config["period"]["end"])
    tz = ZoneInfo(config["period"]["timezone"])
    columns = config["columns"]
    definitions = status_definitions(config)
    aliases = status_aliases(config, metadata.get("status_catalog"))
    configured_names = {s["id"]: s["name"] for s in definitions if s["name"] != s["id"]}
    mapping = {s["id"]: key for key, col in columns.items() for s in col["statuses"]}
    col_keys = list(columns) + [OUTSIDE]
    col_labels = {key: value["name"] for key, value in columns.items()} | {OUTSIDE: "Вне колонок"}
    people, types, priorities, names = {}, {}, {}, {}
    selected, tasks, timelines, all_visits, all_columns, warnings = [], [], {}, [], [], []
    leads, exclusions, deliveries = [], [], []
    age_values, age_open, active_status = defaultdict(list), Counter(), defaultdict(list)
    throughput = config["metrics"]["throughput"]
    lead_cfg = config["metrics"]["lead_time"]
    end_match = boundary_match(throughput["end"], mapping)
    begin_match = boundary_match(throughput["start"], mapping) if throughput["start"] else None
    default_start = boundary_match(lead_cfg["start"], mapping)
    starts_by_type = {type_id: set(ids) for type_id, ids in lead_cfg["start_by_type"].items()}
    lead_basis, aging_basis = lead_cfg["time_basis"], config["metrics"]["aging"]["time_basis"]
    account_aliases, accounts = assignee_aliases(config), set(selected_assignee_ids(config))
    lead_end = boundary_match(lead_cfg["end"], mapping)
    field_cfg = config["fields"]
    seen = set()
    for issue in issues:
        if issue["key"] in seen:
            raise ValueError("Duplicate issue: " + issue["key"])
        seen.add(issue["key"])
        created = instant(issue["created_at"])
        if created >= end or issue["project_key"] != config["jira"]["project_key"] or issue["issue_type"]["id"] not in config["jira"]["issue_type_ids"]:
            continue
        if any(k not in issue["field_values"] or k not in issue["field_history"] for k in field_cfg.values()):
            raise ValueError("Snapshot does not contain all configured fields: " + issue["key"])
        person, person_label = identity(value_before(issue, field_cfg["assignee_id"], end))
        if accounts and person not in accounts:
            continue
        person, person_label = account_aliases.get(person, (person, person_label))
        priority, priority_label = identity(value_before(issue, field_cfg["priority_id"], end))
        people[person], priorities[priority] = person_label, priority_label
        types[issue["issue_type"]["id"]] = issue["issue_type"]["name"]
        t = timeline(issue, end, aliases, configured_names)
        names.update(t["names"])
        warnings.extend(t["warnings"])
        timelines[issue["key"]] = t
        issue["resolved_status_history"] = t["resolutions"]
        all_visits.append(t["visits"])
        column_visits = [(a, b, mapping.get(s, OUTSIDE)) for a, b, s in t["visits"]]
        all_columns.append(column_visits)
        starts, _ = entries_for(t, created, begin_match) if begin_match else ([], [])
        completions, _ = entries_for(t, created, end_match)
        done_in_window = [d for d in completions if start <= d < end and (not begin_match or any(s <= d for s in starts))]
        deliveries.extend({"key": issue["key"], "at": d, "person_id": person,
                           "type_id": issue["issue_type"]["id"], "priority_id": priority} for d in done_in_window)
        current = t["current"]
        issue["status_at_period_end"] = {"id": current, "name": names.get(current, current)}
        issue["assignee_at_period_end"] = {"id": person, "name": person_label}
        issue["priority_at_period_end"] = {"id": priority, "name": priority_label}
        issue["last_completion_at"] = max(completions).isoformat() if completions else None
        done_column_key = config["metrics"]["cfd"]["done_column"]
        done_arrivals, _ = entries_for(t, created, lambda s: mapping.get(s, OUTSIDE) == done_column_key)
        issue["last_done_at"] = max(done_arrivals).isoformat() if done_arrivals else None
        issue["closed_at"] = issue["last_done_at"] if mapping.get(current, OUTSIDE) == done_column_key else None
        selected.append(issue)
        task = {"key": issue["key"], "summary": issue["summary"], "person_id": person,
                "person": person_label, "type_id": issue["issue_type"]["id"], "type": issue["issue_type"]["name"],
                "priority_id": priority, "priority": priority_label, "status": current,
                "column": mapping.get(current, OUTSIDE), "created": created.isoformat()}
        tasks.append(task)
        type_start_ids = starts_by_type.get(task["type_id"])
        lead_start = type_start_ids.__contains__ if type_start_ids else default_start
        firsts, _ = entries_for(t, created, lead_start)
        lasts, _ = entries_for(t, created, lead_end)
        begin = (min(firsts) if lead_cfg["start_event"] == "first_entry" else max(firsts)) if firsts else None
        qualifying = [d for d in lasts if begin is not None and d >= begin]
        reason = None
        if not firsts:
            reason = "missing_start"
        elif not qualifying:
            reason = "missing_completion_after_start"
        else:
            finish = min(qualifying) if lead_cfg["end_event"] == "first_entry" else max(qualifying)
            if begin > finish:
                reason = "start_after_selected_completion"
            elif not start <= finish < end:
                reason = "completion_outside_period"
            elif lead_cfg["require_end_state"] and not lead_end(current):
                reason = "reopened_unfinished"
            else:
                start_source = next(e for e in t["entries"] if instant(e["at"]) == begin and lead_start(e["status_id"]))
                leads.append({**task, "start": begin.isoformat(), "done": finish.isoformat(),
                              "days": duration_days(begin, finish, lead_basis, tz),
                              "start_source": start_source,
                              "legacy_before_start": any(at < begin for at in t["legacy_instants"])})
        if reason and (done_in_window or firsts):
            exclusions.append({"key": issue["key"], "reason": reason, "entered_throughput_end_in_period": bool(done_in_window)})
        for kind, keys, now in (("status", set(t["names"]), current), ("column", col_keys, task["column"])):
            for key in keys:
                match = (lambda s, target=key: s == target) if kind == "status" else (lambda s, target=key: mapping.get(s, OUTSIDE) == target)
                arrivals, exits = entries_for(t, created, match)
                if not arrivals:
                    continue
                known = kind == "column" or key not in t["uncertain"]
                completed = key != now and exits and start <= max(exits) < end
                if key == now:
                    age_open[kind, key, person] += 1
                if known and (completed or (kind == "status" and key == now)):
                    value = sum(duration_days(a, b, aging_basis, tz) for a, b, s in t["visits"] if match(s))
                    if completed:
                        age_values[kind, key, person].append(value)
                    else:
                        active_status[key].append({**task, "entered_at": min(arrivals).isoformat(), "days": value,
                                                   "last_entered_at": max(arrivals).isoformat(),
                                                   "current_visit_days": duration_days(max(arrivals), end, aging_basis, tz)})

    people = dict(sorted(people.items(), key=lambda p: p[1]))
    weeks = []
    monday = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    monday -= timedelta(days=monday.weekday())
    while monday < end:
        stop = monday + timedelta(days=7)
        events = [d for d in deliveries if monday <= d["at"] < stop]
        keys = sorted({d["key"] for d in events})
        weeks.append({"start": monday.isoformat(), "end": stop.isoformat(), "partial": monday < start or stop > end,
                      "keys": keys, "count": len(keys), "transitions": len(events)})
        monday = stop
    full = [w["count"] for w in weeks if not w["partial"]]
    lookup = {t["key"]: t for t in tasks}
    delivered_keys = {d["key"] for d in deliveries}
    groups = {}
    for dimension, labels in (("person", people), ("type", types), ("priority", priorities)):
        rows = []
        for key, name in labels.items():
            counts = [sum(lookup[k][dimension+"_id"] == key for k in w["keys"]) for w in weeks]
            full_counts = [n for n, w in zip(counts, weeks) if not w["partial"]]
            rows.append({"id": key, "name": name, "weeks": counts,
                         "unique": sum(lookup[k][dimension+"_id"] == key for k in delivered_keys),
                         "mean_full": statistics.mean(full_counts) if full_counts else None})
        if any(sum(g["weeks"][i] for g in rows) != w["count"] for i, w in enumerate(weeks)):
            raise ValueError("Throughput grouping failed")
        groups[dimension] = rows
    lead_weeks = []
    for i, w in enumerate(weeks):
        low = instant(weeks[max(0, i-lead_cfg["rolling_weeks"]+1)]["start"])
        high = instant(w["end"])
        values = [lead["days"] for lead in leads if low <= instant(lead["done"]) < high]
        current_values = [lead["days"] for lead in leads if instant(w["start"]) <= instant(lead["done"]) < high]
        lead_weeks.append({"week": w["start"], "partial": w["partial"], "n": len(current_values),
                           "rolling_n": len(values), "rolling_median": statistics.median(values) if values else None})
    width = lead_cfg["histogram_bin_days"]
    histogram = [{"left": i*width, "right": (i+1)*width,
                  "count": sum(i*width <= lead["days"] < (i+1)*width for lead in leads)}
                 for i in range(int(max((lead["days"] for lead in leads), default=0)//width)+1)]
    if sum(b["count"] for b in histogram) != len(leads):
        raise ValueError("Lead histogram failed")
    status_keys = sorted({s for visits in all_visits for a, b, s in visits if max(a, start) < min(b, end)} |
                         {t["status"] for t in tasks} |
                         {key for kind, key, _ in age_values if kind == "status"},
                         key=lambda s: (0, list(mapping).index(s)) if s in mapping else (1, names.get(s, s)))
    done_column = config["metrics"]["cfd"]["done_column"]
    done_statuses = [s["id"] for s in columns[done_column]["statuses"]]
    cfd = {}
    for kind, visits, keys, labels, done_keys in (
        ("columns", all_columns, col_keys, col_labels, [done_column]),
        ("statuses", all_visits, status_keys, names, done_statuses),
    ):
        points, means = occupancy(visits, keys, start, end)
        for point in points:
            at = instant(point["at"])
            expected = sum(instant(t["created"]) <= at for t in tasks)
            if sum(point["counts"]) != expected:
                raise ValueError("CFD inventory does not reconcile")
        cfd[kind] = {"keys": keys, "labels": [labels.get(k, k) for k in keys], "points": points,
                     "done_indices": [i for i, k in enumerate(keys) if k in done_keys],
                     "means": means}
    def age_rows(kind, keys, labels):
        result = []
        for key in keys:
            values = [v for person in people for v in age_values[kind, key, person]]
            result.append({"key": key, "name": labels.get(key, key), "mean": statistics.mean(values) if values else None,
                           "n": len(values), "open": sum(age_open[kind, key, p] for p in people),
                           "by_person": {p: {"mean": statistics.mean(age_values[kind, key, p]) if age_values[kind, key, p] else None,
                                             "n": len(age_values[kind, key, p])} for p in people}})
        return result
    accuracy = calculate_accuracy(selected, timelines, leads, mapping, config)
    terminal = set(done_statuses) | {k for k, v in metadata.get("status_categories", {}).items() if v == "done"}
    status_time = []
    for key in status_keys:
        if key in terminal:
            continue
        completed = [v for p in people for v in age_values["status", key, p]]
        active = sorted(active_status[key], key=lambda a: (-a["days"], a["key"]))
        status_time.append({"key": key, "name": names.get(key, key),
                            "mean": statistics.mean(completed) if completed else None,
                            "n": len(completed), "active": active, "top_tasks": active[:4],
                            "terminal_category_known": key in metadata.get("status_categories", {})})
    board_statuses = list(mapping)
    status_time.sort(key=lambda row: (0, -board_statuses.index(row["key"])) if row["key"] in mapping
                     else (1, row["name"], row["key"]))
    lead_stats = stats([lead["days"] for lead in leads])
    lead_keys = {lead["key"] for lead in leads}
    legacy_lead_keys = {lead["key"] for lead in leads if lead["legacy_before_start"]}
    historical_starts = {lead["key"] for lead in leads if lead["start_source"]["matched_by"] is not None}
    exclusion_reasons = {row["key"]: row["reason"] for row in exclusions}

    def lead_coverage(keys):
        completed, sampled = delivered_keys & keys, lead_keys & keys
        missing = completed - sampled
        if not missing <= exclusion_reasons.keys():
            raise ValueError("Unexplained lead time exclusion")
        return {"throughput_n": len(completed), "matched_n": len(completed & sampled),
                "missing_n": len(missing), "lead_only_n": len(sampled - completed),
                "legacy_before_start_n": len(sampled & legacy_lead_keys),
                "historical_start_n": len(sampled & historical_starts),
                "coverage": len(completed & sampled)/len(completed) if completed else None,
                "exclusion_counts": dict(Counter(exclusion_reasons[key] for key in sorted(missing)))}

    coverage = lead_coverage(set(lookup))
    lead_groups = {}
    for dimension, labels, name in (("person", people, "people"), ("priority", priorities, "priorities"), ("type", types, "types")):
        rows = []
        for key, label in labels.items():
            keys = {t["key"] for t in tasks if t[dimension+"_id"] == key}
            values = [lead["days"] for lead in leads if lead["key"] in keys]
            rows.append({"id": key, "name": label, **lead_comparison(values, lead_stats["p50"]),
                         "cohort": lead_coverage(keys)})
        for counter in ("throughput_n", "matched_n", "missing_n", "lead_only_n", "legacy_before_start_n", "historical_start_n"):
            if sum(row["cohort"][counter] for row in rows) != coverage[counter]:
                raise ValueError("Lead time cohort grouping failed")
        lead_groups[name] = rows
    aging_columns = age_rows("column", col_keys, col_labels)
    breakdown = calculate_breakdown(aging_columns, config)
    updated = []
    incomplete_update_evidence = []
    for i in selected:
        evidence = i.get("change_timestamps", []) + [i["created_at"], i["updated_at"]]
        if any(start <= instant(at) < end for at in evidence):
            updated.append(i["key"])
        elif "change_timestamps" not in i and instant(i["updated_at"]) >= start:
            incomplete_update_evidence.append(i["key"])
    return {"project_key": config["jira"]["project_key"], "project_name": metadata.get("project_name", config["jira"]["project_key"]),
            "start": start.isoformat(), "end": end.isoformat(),
            "timezone": config["period"]["timezone"], "cohort_size": len(tasks), "tasks": tasks,
            "issue_types": [{"id": k, "name": v, "n": sum(t["type_id"] == k for t in tasks)} for k, v in sorted(types.items())],
            "updated_in_period": {"n": len(updated), "keys": sorted(updated), "incomplete_evidence": incomplete_update_evidence},
            "status_time": status_time, "aging_time_basis": aging_basis,
            "people": [{"id": k, "name": v} for k, v in people.items()],
            "throughput": {"weeks": weeks, "groups": groups, "unique": len({d["key"] for d in deliveries}),
                           "events": len(deliveries), "weekly_sum": sum(w["count"] for w in weeks), "full_week_stats": stats(full)},
            "lead": {"time_basis": lead_basis, "stats": lead_comparison([lead["days"] for lead in leads], lead_stats["p50"]), "tasks": leads, "weeks": lead_weeks,
                     "histogram": histogram, "exclusions": exclusions, "cohort": coverage, **lead_groups},
            "cfd_columns": cfd["columns"], "cfd_statuses": cfd["statuses"],
            "mean_statuses": [{"name": cfd["statuses"]["labels"][i], "mean": mean,
                               "initial": cfd["statuses"]["points"][0]["counts"][i],
                               "final": cfd["statuses"]["points"][-1]["counts"][i]} for i, mean in enumerate(cfd["statuses"]["means"])],
            "mean_columns": [{"key": key, "name": cfd["columns"]["labels"][i], "mean": cfd["columns"]["means"][i],
                              "initial": cfd["columns"]["points"][0]["counts"][i],
                              "final": cfd["columns"]["points"][-1]["counts"][i]} for i, key in enumerate(cfd["columns"]["keys"])],
            "flow_breakdown": breakdown,
            "aging_columns": aging_columns,
            "aging_statuses": age_rows("status", status_keys, names), "accuracy": accuracy,
            "warnings": warnings, "validation": {"weekly_groups_match": True, "cfd_population_matches": True, "histogram_matches": True,
                                                   "flow_breakdown_matches": True, "lead_cohorts_match": True}}


def calculate_breakdown(aging_columns, config):
    rows = {r["key"]: r for r in aging_columns}
    segments = [{k: rows[key][k] for k in ("key", "name", "mean", "n")}
                for key in config["columns"] if key != config["metrics"]["cfd"]["done_column"]]
    total = sum(s["mean"] for s in segments if s["mean"] is not None)
    for segment in segments:
        segment["fraction"] = segment["mean"]/total if total > 0 and segment["mean"] is not None else None
    return {"sum_means": total, "segments": segments}


def calculate_accuracy(issues, timelines, leads, mapping, config):
    cfg = config["metrics"]["estimate_accuracy"]
    tz = ZoneInfo(config["period"]["timezone"])
    lookup = {i["key"]: i for i in issues}
    rows, excluded = [], []
    for lead in leads:
        issue, t = lookup[lead["key"]], timelines[lead["key"]]
        def match(s):
            return mapping.get(s, OUTSIDE) in cfg["scope_columns"]
        entries, _ = entries_for(t, instant(issue["created_at"]), match)
        if not entries:
            excluded.append({"key": issue["key"], "reason": "no_estimate_scope_entry"})
            continue
        frozen_at = min(entries)
        raw = value_before(issue, config["fields"]["estimate_id"], frozen_at)
        estimate, reason = estimate_value(raw, cfg)
        if reason:
            excluded.append({"key": issue["key"], "reason": reason})
            continue
        done = instant(lead["done"])
        duration = 0.
        for left, right, state in t["visits"]:
            right = min(right, done)
            if left < right and match(state):
                duration += (seconds(left, right) if cfg["time_basis"] == "calendar" else
                             working_seconds(left, right, cfg["working_calendar"], tz))
        day_hours = 24 if cfg["time_basis"] == "calendar" else (
            datetime.combine(datetime.min.date(), time.fromisoformat(cfg["working_calendar"]["end"])) -
            datetime.combine(datetime.min.date(), time.fromisoformat(cfg["working_calendar"]["start"]))
        ).total_seconds()/3600
        factor = {"seconds": 1, "hours": 3600, "days": day_hours*3600}.get(cfg["unit"])
        ratio = duration/(estimate["value"]*factor) if factor and estimate["value"] > 0 and not estimate["open_ended"] else None
        rows.append({"key": issue["key"], "estimate_id": estimate["id"], "estimate": estimate["label"],
                     "estimate_value": estimate["value"], "estimate_at": frozen_at.isoformat(),
                     "lead_days": lead["days"], "scope_days": duration/(day_hours*3600), "actual_to_estimate": ratio})
    cells = defaultdict(list)
    for row in rows:
        cells[row["estimate_id"], math.floor(row["lead_days"]/cfg["frequency_bin_days"])].append(row)
    bubbles = []
    for (_, bucket), items in cells.items():
        bubbles.append({"estimate_id": items[0]["estimate_id"], "estimate": items[0]["estimate"],
                        "y": items[0]["estimate_value"], "left": bucket*cfg["frequency_bin_days"],
                        "right": (bucket+1)*cfg["frequency_bin_days"], "n": len(items), "keys": [i["key"] for i in items]})
    estimates = {r["estimate_id"]: (r["estimate_value"], r["estimate"]) for r in rows}
    for i, option in enumerate(cfg["options"]):
        estimates.setdefault(option["id"], (i+1 if cfg["unit"] == "ordinal" else option["value"], option["label"]))
    summary = []
    for key, (value, label) in sorted(estimates.items(), key=lambda p: p[1][0]):
        items = [r for r in rows if r["estimate_id"] == key]
        ratios = [r["actual_to_estimate"] for r in items if r["actual_to_estimate"] is not None]
        summary.append({"id": key, "value": value, "name": label, **stats([r["lead_days"] for r in items]),
                        "scope_mean": statistics.mean(r["scope_days"] for r in items) if items else None,
                        "ratio_n": len(ratios), "ratio_median": statistics.median(ratios) if ratios else None,
                        "over_estimate": sum(r > 1 for r in ratios)})
    if sum(b["n"] for b in bubbles) != len(rows):
        raise ValueError("Estimate frequencies do not reconcile")
    return {"unit": cfg["unit"], "time_basis": cfg["time_basis"], "bin_days": cfg["frequency_bin_days"],
            "scope_labels": [config["columns"][key]["name"] for key in cfg["scope_columns"]],
            "n": len(rows), "lead_n": len(leads), "tasks": rows, "bubbles": bubbles,
            "groups": summary, "exclusions": excluded, "exclusion_counts": dict(Counter(r["reason"] for r in excluded))}
