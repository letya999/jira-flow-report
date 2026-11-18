# Metric definitions and limitations

[Русский](metrics.ru.md) · [Back to the README](../README.md)

## Common rules

- The reporting window is **[start, end)**: the start is included and the end is excluded.
  Timestamps require offsets; the configured time zone controls Monday-based weeks.
- Histories reconstruct status, assignee and priority at the exclusive cutoff.
  Displayed person groups use the assignee at that cutoff, not attribution of each person's work.
- The historical cohort includes the initial inventory needed for CFD. The changed-in-period count
  is smaller: creation, current `updated_at`, or any recorded changelog event must fall in the window.
- Flow metrics use configured issue types and assignees. Structural children can have other types or owners.
- Missing starts, inaccessible children and unresolved histories are exclusions or unknowns.
  Exported metrics retain detailed exclusions; absence is not converted to zero.
- Calculations retain full precision. The HTML rounds days to three decimals; 0.001 day is 86.4 seconds.

## Throughput

For each local calendar week, count **distinct issue keys** entering the configured end stage in the window.
If a start boundary is configured, an earlier visit to it is required.
An issue counts once within a week and may count again if closed in another week.

**Full-week average = sum of counts for complete weeks / number of complete weeks.**
Zero-completion weeks are included; incomplete edge weeks are charted but excluded from the average.
Distinct issues over the period, weekly observations and raw completion events can therefore differ.

## Lead time

**Lead days = selected end-entry timestamp − selected start-entry timestamp, divided by 86,400 seconds.**
Boundaries can be a status ID or configured column. First/last entry rules are configurable.
The default requires the issue to remain in the end stage at the cutoff. The selected completion
must fall in the period; the start may be earlier than the period.

The histogram uses equal-width day bins, including the left boundary and excluding the right.
The rolling curve pools issue durations by completion week across the configured number of weeks,
then takes their median; it is not an average of weekly medians.

- **Median/P50:** the middle of sorted durations.
- **Mean:** total days divided by issue count; sensitive to long cases.
- **P85:** the 85th percentile using linear interpolation between sorted observations.
- **Range:** maximum minus minimum duration.
- **Median / overall median:** group typical duration relative to the pooled team median.
- **Median / group mean:** how far the group's middle lies from its mean.

Missing or zero denominators produce no ratio. Lead time measures elapsed delivery time, not effort.
Throughput and lead time may use different endpoints and cohorts; check configured boundaries.

## Cumulative flow (CFD)

At creation and every status event, reconstruct the number of issues in each status or column.
The plot uses exact right-continuous steps, rather than daily sampling.

**Mean inventory = sum(count × duration of that count) / reporting duration.**
Means are time-weighted. The initial Done inventory is subtracted only on the diagrams;
reported occupancy means keep the actual inventory. Reopenings can reduce the Done band.
Historical unmapped statuses stay in exported data and CFD for population accounting.

## Aging time and the stage strip

The default completed-stage duration is **last exit − first entry**, including time elsewhere
between returns. Its last exit must be in the window. Issues still in that stage at cutoff
are excluded from its completed mean. The alternative `sum_visits` method sums only time inside.

**Stage mean = sum of eligible issue durations / eligible issue count.**
Each stage can have a different cohort. The strip reuses the exact column means from table 4.1,
in board order and excluding Done. **Segment width = column mean / sum of displayed known means.**
That sum is not the elapsed lead time of a shared set of tasks.

Person values at or below 75% of the matching team mean are light green; values at or above
125% are light red. Lead median/mean values use their matching pooled statistic.
The median-to-own-mean ratio is not colored as a performance comparison.

## Decomposition

The cohort contains issues changed in the period plus unfinished issues at cutoff.
Old completed stock needed only for CFD is excluded.

**Mean direct children = sum of immediate child counts / issues with known hierarchy.**
Zeros are included. A separate mean considers only decomposed issues.
Depth is the longest path below an issue: 0 for no children, 1 for children, 2 for grandchildren.

Parent/subtask relations and configured directional split-link IDs establish children.
Edges are deduplicated. Parent/link histories and creation dates restore the cutoff tree.
An issue's own epic parent does not mean that issue is decomposed.
Missing/restricted children and cycles are unknown, not proof of no children.

Open unsplit candidates have known zero children and a first-entry age above the completed
mean of their **current status**. This flags long elapsed time, including waiting;
it does not establish a large amount of work. No closed-task warning list is produced.

## Time in status

**Open age = cutoff − first confirmed entry into the current status.**
Returning to a status does not reset age. The comparison threshold is the completed
first-entry/last-exit mean for the same status, with the last exit in the period.
The JSON also retains the current continuous visit for comparison.

Only statuses with above-mean open issues appear, with the four oldest shown per status.
Order follows configured columns/statuses from right to left. Configured Done and statuses in
Jira's Done category are excluded. The completed Aging status table shows configured statuses only.

## Estimates

Freeze the estimate immediately before the first entry into its configured column scope.
**Actual duration = sum of visits in those columns until final completion.**
Time outside scope is excluded. Incomplete work and missing pre-start estimates are excluded.

The frequency chart uses configured lead time on X, ascending estimate value/category on Y,
and bubble area proportional to issue count in the same day bin and estimate category.
The actual in-scope duration used for a time-estimate comparison is different from X lead time.

T-shirt sizes and story points stay ordinal/numeric; no conversion to hours or time-error percentages.
For seconds/hours/days, actual-to-estimate ratios can be calculated. Zero and open-ended estimates
are not exact denominators. Working-time mode uses the configured schedule and holidays;
a working day is its schedule interval. A calendar day is 24 hours.

## Validation and limits

CFD balances, excluded cases, source/config hashes and color checks are exported to `metrics.json`.
Chart colors are checked for pairwise CIE Delta E76 distances, adjacency and contrast with boundaries/text.
These distance thresholds are engineering checks, not a claim of universal color accessibility.
Offline rebuilding reuses exported histories and preserves numerical results for the same settings.

Deleted, hidden or unrecorded Jira information cannot be reconstructed. Renames without historical IDs
and old workflow migrations can affect interpretation. An optional board check compares against the
**current** board mapping, not a historically versioned board. Synthetic tests and a successful build
do not prove completeness of a particular Jira site's data.
