# Benchmarking

Run a set of queries against a cluster, record the timings, and compare — two
clusters against each other, before against after, or the same query over
weeks.

Useful for: sizing, verifying a configuration change did what you hoped,
upgrade regression, and the question that started it — *why is cluster A slower
than cluster B?*

---

## ⛔ Not a SQL editor

The query set screen has a SQL box. What keeps it from being a SQL editor is
not the input, it is the **output**: a run gives back milliseconds. The rows
Trino produces are counted and discarded, and no endpoint returns one.

If you need to see results, use Superset or the CLI. A request to show them
here is not a feature request, it is a request to reopen that boundary.

---

## Setup

```yaml
benchmark:
  enabled: true
  default_repetitions: 3
  max_repetitions: 20
  timeout_seconds: 600
```

Also required:

- **`ExecuteQuery`** on the TMS Trino account. Benchmarks run real SQL
- **The Gateway** (`gateway.enabled`), ideally. Without it TMS cannot tell
  whether a cluster is in rotation, so every run is recorded as though it were
  serving traffic

Nothing ships by default — no bundled query set. A bundled set would have to
name a catalog, and which catalogs exist is a fact about your deployment. A
default that fails on first use teaches you the feature is broken.

---

## Query sets

A set is the queries that run together and get compared together. Create one
from the console; the set and its first query are one form, because a set with
no queries cannot be run.

**Only read-only statements are accepted** — `SELECT`, `WITH`, `SHOW`,
`EXPLAIN`, `DESCRIBE`, `VALUES`, `TABLE` — checked when you save and **again
immediately before each execution**. Comments cannot hide a `DELETE`, and a
statement cannot be chained with `;`.

That double check is what replaced code review. These used to live in a
git-reviewed YAML file; they moved into the database so that adding a query
does not need a deploy, and the allowlist plus the audit trail are the price of
that.

> **The set id cannot be changed.** Runs are recorded against it and only runs
> of the same set can be compared, so renaming would orphan every measurement.

---

## Running

Pick clusters — several at once — a set, repetitions, and a reason.

### Repetitions and the median

Results fold into a **median**, never a mean. The first execution of a query is
not like the rest; a single cold start moves a mean of three by a third.
`repetitions` exists precisely for that.

### "Rows read" and why it varies

`processedRows` counts rows the engine had to **touch**, not rows returned.
Under a `LIMIT` it stops as soon as the limit is met, so the same query can read
a different number each run depending on how splits were scheduled.

When repetitions read different amounts, the screen marks the range — those
timings are not measurements of equal work, and the median above them is a
median of different amounts of work.

---

## Running against a serving cluster

**Allowed, and recorded.** Originally TMS refused to benchmark a cluster still
in rotation. That gate was removed, because the actual need is measuring
production clusters periodically to see a trend — and a gate means the state
people actually use can never be measured.

So the condition travels with the numbers instead:

| | |
|---|---|
| `Quiet` | Out of rotation and idle |
| `Serving traffic` | In rotation, or queries were running. The reason is written out on the row |

Comparing a `Quiet` run with a `Serving traffic` one raises a warning. Two busy
runs compare fine; **one of each** is what ruins a comparison, and a warning
printed on every comparison is a warning on none.

> ⚠️ A heavy set on a serving cluster competes with production for the same
> workers. It can cause the slowdown it is measuring. Start with a light set
> and one repetition.

**TMS will not take a cluster out of rotation for you.** It checks and reports.
A button that did it would be a way around the [safe restart
sequence](safe-restart.md).

Two runs on one cluster are refused — they would measure each other.

---

## Comparing

Open a run, pick another of the same set. Different sets are not offered: the
same query name in two sets is not the same query.

The comparison is **per query**, not one headline number. A single aggregate
lets one pathological query swing the verdict and hide the nine that are
identical — and the pathological one is usually the finding.

Anything under ±5% counts as unchanged; below that is noise dressed as a
finding.

### When a query changed underneath you

A set can be edited, so "same name" stops meaning "same query". Each run stores
a snapshot of the SQL it executed, and a row whose statement differs between the
two runs is marked **SQL changed between these runs**.

Without that the table would show a confident percentage for two different
statements.

---

## Trends

`Benchmark → a set → a query → History` charts how long that query takes over
time, one line per cluster.

| | |
|---|---|
| **Grouping** | Every run, daily, or monthly. A benchmark that runs several times a day is noise at one dot per run |
| **Each point** | The **median** of that group's executions |
| **The dashed line** | The **average of the points drawn** — not of every execution. A line computed from a different population than the dots would lie about them |
| **The legend** | Click to hide a cluster. Colours follow the cluster, so hiding one never repaints the others |

The y-axis always starts at zero, and there is never a second y-axis. An axis
starting at the lowest sample turns a 3% difference into a cliff.

With fewer than two points per cluster nothing is drawn — two dots and no line
is a chart pretending to be a trend. The summary table says the same thing
without the pretence.

---

## Schedules

A trend needs points taken the same way at a regular interval. Schedules do that
without anybody remembering.

```
Benchmark → Schedules → New schedule
```

Interval, first run, clusters, set, repetitions, and a reason.

### ⛔ Nobody is watching when these run

This is the only thing in TMS that writes to a production cluster with no
person present. Every write needs an actor and a reason, and there is no
exception for "nobody was there":

| | |
|---|---|
| **Actor** | Whoever created the schedule — the person who authorised these runs |
| **Reason** | The schedule's reason, carried onto every run. It cannot be blank |
| **Audit** | `BENCHMARK_RUN` per run; `BENCHMARK_SCHEDULE_CHANGE` when a schedule is created, paused or deleted — it authorises *future* unattended runs |

### How it behaves

| | |
|---|---|
| **Interval** | Minimum 15 minutes. Not advice — a benchmark takes cluster capacity, and more often than that is a load generator |
| **Drift** | The next run is computed from the *scheduled* time, so a daily 03:00 schedule stays at 03:00 instead of sliding later by however long each run takes |
| **Missed runs** | Collapse into one. TMS being down over a weekend must not produce a burst of catch-up benchmarks on Monday morning |
| **Already running** | The turn is **skipped**, and skipping is not counted as a failure — the guard doing its job must not pause the schedule |
| **Repeated failures** | Three in a row and the schedule switches itself off, showing `paused by TMS` with the reason. A broken set running every night forever is load with nobody reading it |

Re-enabling clears the failure count: you are saying you dealt with the cause.

Not built, on purpose: a health gate ("only run when the cluster is GOOD") and
a cap on how many clusters run at once. Both are one line each in
`bench/service.py` if you want them.

---

## What is kept

Runs, per-execution results, the SQL each run used, and the routing condition
at the time. `benchmark_result` is append-only at the database level — if
`UPDATE` on it succeeds as `tms_app`, the grants migration was not applied.

Deleting a query set leaves past runs alone. Runs hold their set **by value**:
each one carries a snapshot of what it executed, so deleting removes the set
from future runs and takes nothing away from the measurements already taken.
