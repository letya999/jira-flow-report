# Jira Flow Report

## Commands
- `uv sync --locked`
- `uv run --locked jira-flow run --config configs/local.yaml`
- `uv run --locked jira-flow build --config configs/local.yaml --input <run>/issues.json`
- `uv run --locked jira-flow serve --directory <run> --port 0`
- `uv run --locked pytest`
- `uv run --locked ruff check src tests`

## Contract
- One YAML per team; only `assignee_ids`, no team ID or display label.
- YAML and repeated `--set path=value` arguments feed the same configuration validator.
- Jira field and status matching uses IDs. Names are display metadata only.
- System field IDs such as `assignee` are IDs, not translated labels.
- Keep credentials in `.env` or environment variables; never print or export them.
- Each output directory is `<project_key>-<UTC timestamp>` and contains exactly:
  `issues.csv`, `issues.json`, `config_<project_key>_<timestamp>.yaml`, `report.html`, `metrics.json`.
- Do not add output manifests, logs, archives, nested period/team directories or conclusions sections.
- Edit the packaged HTML template and regenerate; do not patch generated reports.

## Calculation rules
- Datetimes must have offsets. The reporting window is [start, end).
- Reconstruct assignee, priority and status at the exclusive period end.
- Keep complete status histories, including pre-period events and post-cutoff events needed for rollback.
- Do not select only recently updated issues: CFD needs the initial population.
- Preserve blank-ID historical statuses by name. Do not guess aliases to configured IDs.
- Missing history, ambiguous renames, absent starts and missing estimates must be visible as exclusions.
- Throughput is distinct issue keys per local Monday-based week; full-week average includes zeros.
- Lead boundaries are configurable. Default: first development entry to last Done entry, still Done at cutoff.
- CFD uses exact right-continuous event-time steps and time-weighted occupancy. Shift only Done on plots.
- Aging first-entry/last-exit spans include intervening visits elsewhere; sums of visits do not.
- Estimate values are frozen before the first entry into their configured scope.
- Validate field chains only for changes being rolled back; pre-cutoff option IDs may have
  been remapped by Jira imports without a recorded field change. Do not invent an alias.
- Accuracy actual time sums visits in scope until final completion. Incomplete work is excluded.
- Estimate frequency chart: X = configured lead time in days; Y = numeric/ordered estimate ascending.
  Bubble area = issues in the same configured X bin and estimate category. Explain bin width in the report.
- Never convert T-shirt sizes or story points to hours, or report time-error percentages for them.

## Checks
- Cover cutoff boundaries, reopenings, same-column moves, estimate changes after starting, initial status
  at creation, config/CLI equivalence, missing estimates, ordinal ordering and frequency conservation.
- Rebuild from exported issues without Jira and compare numerical metrics.
- Verify seven sections, nine requested charts, offline fonts/scripts and no page overflow in a real browser.
- Status-time alerts compare age since first entry with completed first-entry/last-exit spans ending in the period.
  Exclude configured Done and statuses in Jira's Done category; never silently classify unknown legacy names.
- Preserve timestamps of all changelog groups for the period-update counter, including unrelated fields.
- Status-time display follows columns/statuses right to left in config order; show at most four oldest
  above-mean tasks per status. Unmapped historical statuses follow mapped statuses without guessing order.
- The Aging strip reuses the exact column means/counts from table 4.1 in board order, excluding Done
  and outside-column diagnostics. Width = column mean / sum of displayed known means.
  Missing means stay null. The sum of independent stage means is not a task's total elapsed time.
- Lead comparison ratios are median/group mean and median/pooled overall median; absent or zero bases yield null.
- Show light green at <= 75% and light red at >= 125% of the matching team statistic; no low-sample badges.
- Detailed lead exclusions stay in metrics.json; avoid duplicating diagnostic key lists in the report.
- Do not render statuses with no above-mean tasks in section 6. Section 4.2 shows configured status IDs only;
  retain historical status data in exported JSON and CFD for population accounting.
- Validate chart palettes at build time: all-pair Delta E76 >= 20, actual adjacent layers >= 30,
  contrast with white boundaries >= 3 and label text >= 4.5. Use checked colours in every CFD and flow strip.
- Label Aging segments as stage means, never as contributions to a shared total lead time.
- Never replace missing data with zeros. An empty section keeps an explicit explanation.
- Keep full precision in ages, means and ratios; round only in HTML. Batch transitions may yield the
  same displayed age. Show the first-entry timestamp for open decomposition candidates.
- Decomposition is section 5; status time and estimates are sections 6 and 7.
  Include tasks changed in the period plus unchanged unfinished tasks; exclude old completed CFD stock.
  Fetch structural children regardless of assignee/type, without adding subtasks/epics to flow metrics.
  Count parent-child relations and configured split link type IDs, deduplicate children and detect cycles.
  Restore parent/link changes and child creation times at cutoff.
  Old Link/IssueParentAssociation events have no fieldId: hierarchy needs unfiltered changelogs.
  Compare open first-status-entry age with that status's completed mean. Do not add closed-task warnings.
  Missing/restricted children and cycles are unknown, never evidence of no decomposition.
- Use synthetic fixtures for numerical regression; tests must not depend on private configs, Jira or local reports.

## Public repository and Git
- The sole long-lived branch is `main`. Do not create `dev` or feature branches for this project.
- Commit subject: `type(scope): description`; scope is optional. Allowed types:
  `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`. One logical change per commit.
- Never add `Co-authored-by` trailers, AI attribution or additional commit authors.
- Use the configured human Git identity; never invent or override author/committer details.
- Install versioned hooks with `git config core.hooksPath .githooks`; never bypass them.
- Publishing, `git push` and changing remote access require an explicit user request.
- Only `configs/example.yaml` is public. It contains synthetic IDs and English instructions.
- Keep concrete team/person configs, `.env`, caches and all report runs out of Git and distributions.
- Public tests and documentation must contain only synthetic task/person data.
- Before committing: run tests and Ruff, inspect staged paths and scan the public files for secrets.
