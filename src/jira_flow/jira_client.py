"""Read-only Jira Cloud access and complete, reusable issue snapshots."""

import base64
import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .config import instant, metric_status_ids, selected_assignee_ids, status_definitions
from .decomposition import export_hierarchy


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a Jira Authorization header to a redirect destination.
        return None


class JiraClient:
    def __init__(self, env_file=".env", cache_dir=".cache"):
        load_dotenv(env_file, override=False)
        self.base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
        url = urlsplit(self.base)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment or url.path:
            raise ValueError("JIRA_BASE_URL must be an HTTPS origin without credentials or a path")
        if not email or not token:
            raise ValueError("Set JIRA_EMAIL and JIRA_API_TOKEN in .env or environment")
        self._auth = "Basic " + base64.b64encode((email + ":" + token).encode()).decode()
        self._opener = build_opener(_NoRedirect())
        instance = hashlib.sha256((self.base + "|" + email).encode()).hexdigest()[:16]
        self.cache = Path(cache_dir) / instance
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, path, params=None):
        return self._request(path, params=params)

    def _request(self, path, params=None, payload=None):
        if not path.startswith("/rest/") or ".." in path:
            raise ValueError("Only Jira REST paths are accepted")
        if payload is not None and path != "/rest/api/3/changelog/bulkfetch":
            raise ValueError("POST is restricted to the read-only bulk changelog endpoint")
        url = self.base + path + ("?" + urlencode(params) if params else "")
        for attempt in range(5):
            try:
                request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                  headers={"Authorization": self._auth, "Accept": "application/json", "Content-Type": "application/json"})
                with self._opener.open(request, timeout=45) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise RuntimeError(f"Jira HTTP {exc.code}: {path}") from None
                try:
                    delay = float(exc.headers.get("Retry-After", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(max(delay, 0), 30))
            except (URLError, TimeoutError):
                if attempt == 4:
                    raise RuntimeError("Jira connection failed: " + path) from None
                time.sleep(2 ** attempt)
        raise RuntimeError("Jira retries exhausted")

    def search(self, jql, field_ids):
        rows, token, tokens, seen = [], None, set(), set()
        while True:
            params = {"jql": jql, "fields": ",".join(field_ids), "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            page = self.get("/rest/api/3/search/jql", params)
            for issue in page["issues"]:
                if issue["id"] in seen:
                    raise ValueError("Duplicate Jira search issue: " + issue["key"])
                seen.add(issue["id"])
                rows.append(issue)
            if page.get("isLast"):
                return rows
            token = page.get("nextPageToken")
            if not token or token in tokens:
                raise ValueError("Missing/repeated Jira search page token")
            tokens.add(token)

    def bulk_changelog(self, issue_ids, field_ids=None):
        rows = {str(key): [] for key in issue_ids}
        for offset in range(0, len(issue_ids), 1000):
            batch = [str(key) for key in issue_ids[offset:offset+1000]]
            token, tokens, seen = None, set(), {}
            while True:
                body = {"issueIdsOrKeys": batch, "maxResults": 1000}
                if field_ids is not None:
                    body["fieldIds"] = field_ids
                if token:
                    body["nextPageToken"] = token
                page = self._request("/rest/api/3/changelog/bulkfetch", payload=body)
                for group in page["issueChangeLogs"]:
                    key = str(group["issueId"])
                    if key not in batch:
                        raise ValueError("Unexpected issue in bulk changelog")
                    for history in group["changeHistories"]:
                        if type(history["created"]) is int:
                            history["created"] = datetime.fromtimestamp(history["created"]/1000, timezone.utc).isoformat()
                        instant(history["created"])
                        identity = key, history["id"]
                        if identity in seen:
                            previous = seen[identity]
                            if instant(previous["created"]) != instant(history["created"]):
                                raise ValueError("Inconsistent repeated bulk changelog record")
                            # Jira can split one changelog group across result pages.
                            counts = Counter(json.dumps(item, sort_keys=True) for item in previous["items"])
                            incoming = Counter()
                            for item in history["items"]:
                                signature = json.dumps(item, sort_keys=True)
                                incoming[signature] += 1
                                if incoming[signature] > counts[signature]:
                                    previous["items"].append(item)
                        else:
                            seen[identity] = history
                            rows[key].append(history)
                token = page.get("nextPageToken")
                if not token:
                    break
                if token in tokens:
                    raise ValueError("Repeated bulk changelog page token")
                tokens.add(token)
            print(f"Bulk histories: {min(offset+1000, len(issue_ids))}/{len(issue_ids)}", flush=True)
        return rows

    def changelog(self, issue_id):
        rows, seen, offset, total = [], set(), 0, None
        while True:
            page = self.get(f"/rest/api/3/issue/{issue_id}/changelog", {"startAt": offset, "maxResults": 100})
            if page["startAt"] != offset or (total is not None and total != page["total"]):
                raise ValueError("Changelog changed during pagination: " + issue_id)
            total = page["total"]
            values = page["values"]
            for row in values:
                if row["id"] in seen:
                    raise ValueError("Duplicate changelog record: " + issue_id)
                seen.add(row["id"])
            rows.extend(values)
            offset += len(values)
            if offset == total:
                if page.get("isLast") is False:
                    raise ValueError("Incorrect changelog last page: " + issue_id)
                return rows
            if not values or offset > total or page.get("isLast"):
                raise ValueError("Incomplete changelog: " + issue_id)

    def status_catalog(self, project_id, project_statuses):
        """Include unused statuses when the caller has access to the project catalog."""
        rows, offset, total = [], 0, None
        while True:
            try:
                page = self.get("/rest/api/3/statuses/search", {"projectId": project_id,
                                "includeGlobalStatuses": "true", "startAt": offset, "maxResults": 100})
            except RuntimeError as exc:
                if offset or not any(code in str(exc) for code in ("HTTP 403:", "HTTP 404:")):
                    raise
                available = {s["id"]: s for row in project_statuses for s in row["statuses"]}
                return list(available.values()), False
            if page["startAt"] != offset or (total is not None and total != page["total"]):
                raise ValueError("Status catalog changed during pagination")
            total = page["total"]
            rows.extend(page["values"])
            offset += len(page["values"])
            if offset == total:
                if len({s["id"] for s in rows}) != len(rows):
                    raise ValueError("Duplicate status catalog ID")
                return rows, True
            if not page["values"] or offset > total or page.get("isLast"):
                raise ValueError("Incomplete status catalog")

    def collect(self, issue, field_ids):
        if not str(issue["id"]).isdigit():
            raise ValueError("Jira issue IDs must be numeric")
        path = "/rest/api/3/issue/" + issue["id"]
        params = {"fields": ",".join(field_ids)}
        cache_file = self.cache / (issue["id"] + ".json")
        for attempt in range(3):
            before = self.get(path, params)
            cached = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
            if cached and cached["field_ids"] == field_ids and cached["issue"]["fields"] == before["fields"]:
                return cached
            histories = self.changelog(issue["id"])
            after = self.get(path, params)
            if before["fields"] != after["fields"]:
                if attempt == 2:
                    raise ValueError("Issue kept changing during collection: " + issue["key"])
                continue
            result = {"issue": after, "histories": histories, "field_ids": field_ids}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache, suffix=".tmp", delete=False) as handle:
                temp = Path(handle.name)
                json.dump(result, handle, ensure_ascii=False)
            try:
                temp.replace(cache_file)
            finally:
                temp.unlink(missing_ok=True)
            return result
        raise RuntimeError("Unstable issue: " + issue["key"])

    def fetch_hierarchy(self, config):
        project = json.dumps(config["jira"]["project_key"])
        fields = ["summary", "issuetype", "created", "updated", "parent", "subtasks", "issuelinks"]
        rows = self.search("project = " + project + " ORDER BY id ASC", fields)
        project_count = len(rows)
        print(f"Hierarchy project issues: {project_count}", flush=True)
        # Unfiltered history is required: old Link/IssueParentAssociation events lack fieldId.
        histories = self.bulk_changelog([r["id"] for r in rows])
        reverse = self.search("project = " + project + " ORDER BY id DESC", ["updated"])
        if {r["id"] for r in reverse} != {r["id"] for r in rows}:
            raise ValueError("Project population changed during collection; run again")
        updated = {r["id"]: r["fields"]["updated"] for r in reverse}
        for index, issue in enumerate(rows):
            if updated[issue["id"]] != issue["fields"]["updated"]:
                fresh = self.collect(issue, fields)
                rows[index], histories[issue["id"]] = fresh["issue"], fresh["histories"]
        types = self.get("/rest/api/3/issueLinkType")["issueLinkTypes"]
        known = {r["id"] for r in rows}
        split_ids = config["metrics"]["decomposition"]["split_link_type_ids"]
        index = 0
        while index < len(rows):
            row = rows[index]
            children = [c["id"] for c in row["fields"].get("subtasks", [])]
            children.extend(link[side]["id"] for link in row["fields"].get("issuelinks", [])
                            if link["type"]["id"] in split_ids for side in ("outwardIssue",) if side in link)
            for child in children:
                if child not in known:
                    fresh = self.collect({"id": child, "key": child}, fields)
                    rows.append(fresh["issue"])
                    histories[child] = fresh["histories"]
                    known.add(child)
            index += 1
        return export_hierarchy(rows, histories, types, project_count)

    def fetch(self, config):
        fields = {f["id"]: f for f in self.get("/rest/api/3/field")}
        requested = list(config["fields"].values())
        for field_id in requested:
            if field_id not in fields:
                raise ValueError("Unknown Jira field ID: " + field_id)
        if fields[config["fields"]["assignee_id"]].get("schema", {}).get("type") != "user":
            raise ValueError("The assignee field must be a single-user Jira field")
        project = self.get("/rest/api/3/project/" + config["jira"]["project_key"])
        allowed_types = {t["id"]: t for t in project["issueTypes"]}
        for type_id in config["jira"]["issue_type_ids"]:
            if type_id not in allowed_types or allowed_types[type_id].get("subtask") or allowed_types[type_id].get("hierarchyLevel", 0) > 0:
                raise ValueError("Unknown, epic or subtask issue type: " + type_id)
        profile = self.get("/rest/api/3/myself")
        account_tz = ZoneInfo(profile.get("timeZone", "UTC"))
        end = instant(config["period"]["end"])
        if end > datetime.now(timezone.utc):
            raise ValueError("Cannot fetch a future reporting cutoff")
        upper = (end.astimezone(account_tz) + timedelta(days=1)).strftime("%Y-%m-%d")
        cutoff = end.astimezone(account_tz).strftime("%Y-%m-%d %H:%M")
        quote = json.dumps
        jql = ("project = " + quote(config["jira"]["project_key"]) + " AND issuetype in (" +
               ",".join(config["jira"]["issue_type_ids"]) + ") AND created < " + quote(upper))
        accounts = selected_assignee_ids(config)
        if accounts:
            field = config["fields"]["assignee_id"]
            clause = "cf[" + field.removeprefix("customfield_") + "]" if field.startswith("customfield_") else field
            jql += " AND (" + clause + " in (" + ",".join(map(quote, accounts)) + ") OR updated >= " + quote(cutoff) + ")"
        jql += " ORDER BY key ASC"
        field_ids = sorted(set(requested + ["summary", "status", "issuetype", "project", "created", "updated", "resolutiondate"]))
        issues, token, tokens = [], None, set()
        while True:
            params = {"jql": jql, "fields": ",".join(field_ids), "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            page = self.get("/rest/api/3/search/jql", params)
            issues.extend(i for i in page["issues"] if instant(i["fields"]["created"]) < end)
            if page.get("isLast"):
                break
            token = page.get("nextPageToken")
            if not token or token in tokens:
                raise ValueError("Missing/repeated Jira search page token")
            tokens.add(token)
        if len({i["id"] for i in issues}) != len(issues):
            raise ValueError("Duplicate Jira search issues")
        print(f"Jira candidates: {len(issues)}", flush=True)
        records = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(self.collect, issue, field_ids) for issue in issues]
            for number, future in enumerate(as_completed(futures), 1):
                records.append(future.result())
                if number % 50 == 0 or number == len(futures):
                    print(f"Complete histories: {number}/{len(futures)}", flush=True)
        project_statuses = self.get("/rest/api/3/project/" + config["jira"]["project_key"] + "/statuses")
        catalog, catalog_complete = self.status_catalog(project["id"], project_statuses)
        status_ids = {str(s["id"]) for row in project_statuses for s in row["statuses"]}
        status_ids.update(str(s["id"]) for s in catalog)
        status_ids.update(str(item[side]) for record in records for h in record["histories"]
                          for item in h["items"] if (item.get("fieldId") or item.get("field")) == "status"
                          for side in ("from", "to") if item.get(side))
        configured = {s["id"] for s in status_definitions(config)}
        configured.update(metric_status_ids(config))
        if configured-status_ids:
            raise ValueError("Unknown configured status IDs: " + ", ".join(sorted(configured-status_ids)))
        board = self.get(f"/rest/agile/1.0/board/{config['jira']['board_id']}/configuration") if config["jira"]["board_id"] else None
        return {
            "base_url": self.base, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "project_name": project["name"],
            "status_categories": {str(s["id"]): s.get("statusCategory", {}).get("key")
                                  for row in project_statuses for s in row["statuses"]},
            "query": jql, "query_timezone": str(account_tz),
            "field_metadata": {key: fields[key] for key in requested},
            "board": board, "status_ids": sorted(status_ids),
            "status_catalog": catalog, "status_catalog_complete": catalog_complete,
            "records": sorted(records, key=lambda r: int(r["issue"]["id"])),
        }


def export_issue(record, config):
    issue, histories = record["issue"], record["histories"]
    f = issue["fields"]
    fields = set(config["fields"].values())
    status_history, field_history = [], {key: [] for key in fields}
    for h in sorted(histories, key=lambda h: (instant(h["created"]), int(h["id"]))):
        for item in h["items"]:
            field_id = item.get("fieldId") or item.get("field")
            event = {"at": h["created"], "history_id": h["id"],
                     "from": item.get("from"), "from_label": item.get("fromString"),
                     "to": item.get("to"), "to_label": item.get("toString"),
                     "actor": {"id": h.get("author", {}).get("accountId"),
                               "name": h.get("author", {}).get("displayName")}}
            if field_id == "status":
                status_history.append(event)
            elif field_id in fields:
                field_history[field_id].append(event)
    return {
        "id": issue["id"], "key": issue["key"], "summary": f["summary"],
        "project_key": f["project"]["key"],
        "issue_type": {"id": f["issuetype"]["id"], "name": f["issuetype"]["name"]},
        "created_at": f["created"], "updated_at": f["updated"],
        "change_timestamps": sorted({h["created"] for h in histories}, key=instant),
        "resolution_at": f.get("resolutiondate"), "closed_at": None,
        "current_status": {"id": f["status"]["id"], "name": f["status"]["name"]},
        "field_values": {key: f.get(key) for key in fields},
        "status_history": status_history, "field_history": field_history,
    }
