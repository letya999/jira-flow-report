"""Reconstruct parent/split trees at the reporting cutoff and at completion."""

import statistics
from collections import defaultdict

from .config import instant


PARENT_FIELDS = {"parent", "IssueParentAssociation"}
LINK_FIELDS = {"issuelinks", "Link"}


def export_hierarchy(issues, histories, link_types, verified_count):
    nodes = []
    for issue in issues:
        f = issue["fields"]
        changes = [{"at": h["created"], "id": h["id"], "items": [item for item in h["items"]
                    if (item.get("fieldId") or item.get("field")) in PARENT_FIELDS | LINK_FIELDS]}
                   for h in histories[issue["id"]]]
        nodes.append({"id": issue["id"], "key": issue["key"], "summary": f["summary"],
                      "created_at": f["created"], "updated_at": f["updated"],
                      "issue_type": {k: f["issuetype"].get(k) for k in ("id", "name", "subtask", "hierarchyLevel")},
                      "parent_id": f.get("parent", {}).get("id"),
                      "subtask_ids": [row["id"] for row in f.get("subtasks", [])],
                      "links": [{"type_id": row["type"]["id"],
                                 "outward_id": row.get("outwardIssue", {}).get("id"),
                                 "inward_id": row.get("inwardIssue", {}).get("id")}
                                for row in f.get("issuelinks", [])],
                      "changes": [h for h in changes if h["items"]]})
    return {"nodes": sorted(nodes, key=lambda n: int(n["id"])), "link_types": link_types,
            "verified_project_count": verified_count}


class Hierarchy:
    def __init__(self, data, split_ids):
        self.nodes = {n["id"]: n for n in data["nodes"]}
        self.by_key = {n["key"]: n["id"] for n in data["nodes"]}
        if len(self.nodes) != len(data["nodes"]) or len(self.by_key) != len(self.nodes):
            raise ValueError("Duplicate hierarchy node")
        types = {t["id"]: t for t in data["link_types"]}
        if set(split_ids) - types.keys():
            raise ValueError("Unknown split link type ID")
        self.phrases = [(types[k][direction], direction) for k in split_ids for direction in ("outward", "inward")]
        if any(not phrase for phrase, _ in self.phrases) or len({p for p, _ in self.phrases}) != len(self.phrases):
            raise ValueError("Split link directions require distinct nonempty labels")
        self.parents = {n["id"]: n["parent_id"] for n in data["nodes"] if n["parent_id"]}
        self.splits = set()
        self.declared = set()
        for n in data["nodes"]:
            for child in n["subtask_ids"]:
                # A missing/inconsistent child remains unresolved, never silently zero.
                self.declared.add((n["id"], child))
            for link in n["links"]:
                if link["type_id"] in split_ids:
                    if link["outward_id"]:
                        self.splits.add((n["id"], link["outward_id"]))
                    if link["inward_id"]:
                        self.splits.add((link["inward_id"], n["id"]))
        self.events = sorted([(instant(h["at"]), int(h["id"]), n["id"], item)
                              for n in data["nodes"] for h in n["changes"] for item in h["items"]],
                             key=lambda row: (row[0], row[1]), reverse=True)
        self.cache = {}

    def resolve(self, value, label=None):
        if value is None:
            return None
        return self.by_key.get(str(value), self.by_key.get(str(label), str(value)))

    def link_edge(self, owner, value, label):
        if value is None or not label:
            return None
        other = self.resolve(value)
        matches = [direction for phrase, direction in self.phrases if
                   label.casefold().endswith((phrase + " " + str(value)).casefold())]
        if len(matches) > 1:
            raise ValueError("Ambiguous historical split link")
        if not matches:
            return None
        return (owner, other) if matches[0] == "outward" else (other, owner)

    def at(self, cutoff):
        if cutoff in self.cache:
            return self.cache[cutoff]
        parents, splits = dict(self.parents), set(self.splits)
        for at, _, owner, item in self.events:
            if at < cutoff:
                break
            field = item.get("fieldId") or item.get("field")
            if field in PARENT_FIELDS:
                previous = self.resolve(item.get("from"), item.get("fromString"))
                if previous:
                    parents[owner] = previous
                else:
                    parents.pop(owner, None)
            else:
                added = self.link_edge(owner, item.get("to"), item.get("toString"))
                removed = self.link_edge(owner, item.get("from"), item.get("fromString"))
                if added:
                    splits.discard(added)
                if removed:
                    splits.add(removed)
        existing = {key for key, n in self.nodes.items() if instant(n["created_at"]) < cutoff}
        children = defaultdict(set)
        for child, parent in parents.items():
            if child in existing:
                children[parent].add(child)
        for parent, child in splits:
            if child not in self.nodes or child in existing:
                children[parent].add(child)
        unresolved = defaultdict(set)
        for parent, child in self.declared:
            if child not in self.nodes:
                unresolved[parent].add(child)
            elif child in existing and self.parents.get(child) != parent:
                unresolved[parent].add(child)
        self.cache[cutoff] = children, unresolved
        return children, unresolved

    def measure(self, key, cutoff):
        root = self.by_key.get(key)
        if root is None:
            return {"direct": None, "descendants": None, "depth": None, "problems": ["missing_root"]}
        children, unresolved = self.at(cutoff)
        descendants, problems = set(), set()

        def walk(owner, path):
            if owner in path:
                problems.add("cycle:" + self.nodes.get(owner, {}).get("key", owner))
                return 0
            if owner not in self.nodes:
                problems.add("missing_child:" + owner)
                return 0
            for child in unresolved[owner]:
                problems.add("inconsistent_child:" + child)
            depth = 0
            for child in sorted(children[owner]):
                descendants.add(child)
                depth = max(depth, 1 + walk(child, path | {owner}))
            return depth

        depth = walk(root, set())
        if problems:
            return {"direct": None, "descendants": None, "depth": None, "problems": sorted(problems)}
        return {"direct": len(children[root]), "descendants": len(descendants), "depth": depth,
                "children": sorted(self.nodes[c]["key"] for c in children[root]), "problems": []}


def calculate_decomposition(data, metrics, config):
    if data is None:
        return {"available": False, "reason": "Snapshot has no hierarchy; fetch fresh Jira data"}
    graph = Hierarchy(data, config["metrics"]["decomposition"]["split_link_type_ids"])
    cutoff = instant(metrics["end"])
    active = {a["key"]: {**a, "baseline": row["mean"], "status_name": row["name"]}
              for row in metrics["status_time"] for a in row["active"]}
    cohort = set(metrics["updated_in_period"]["keys"]) | active.keys()
    rows, excluded, open_large = [], [], []
    for task in metrics["tasks"]:
        if task["key"] not in cohort:
            continue
        value = graph.measure(task["key"], cutoff)
        row = {**task, **value}
        (excluded if value["problems"] else rows).append(row)
        a = active.get(task["key"])
        if not value["problems"] and value["direct"] == 0 and a and a["baseline"] is not None and a["days"] > a["baseline"]:
            open_large.append({**row, "days": a["days"], "baseline": a["baseline"],
                               "entered_at": a["entered_at"],
                               "ratio": a["days"]/a["baseline"] if a["baseline"] > 0 else None,
                               "status_name": a["status_name"], "reference_at": metrics["end"]})
    def summary(items):
        decomposed = [r for r in items if r["direct"] > 0]
        return {"n": len(items), "decomposed": len(decomposed),
                "share": len(decomposed)/len(items) if items else None,
                "mean_children": statistics.mean(r["direct"] for r in items) if items else None,
                "mean_children_decomposed": statistics.mean(r["direct"] for r in decomposed) if decomposed else None,
                "mean_depth": statistics.mean(r["depth"] for r in items) if items else None,
                "max_depth": max((r["depth"] for r in items), default=None)}

    return {"available": True, "cohort_n": len(cohort), "stats": summary(rows), "tasks": rows,
            "people": [{**person, **summary([r for r in rows if r["person_id"] == person["id"]])} for person in metrics["people"]],
            "excluded": excluded,
            "open_large": sorted(open_large, key=lambda r: -r["days"]),
            "structural_nodes": len(data["nodes"]), "verified_project_count": data["verified_project_count"]}
