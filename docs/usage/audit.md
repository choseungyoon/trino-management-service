# Audit

Every write TMS performs, recorded with who, what, why and when — and
structurally impossible to skip.

---

## The guarantee

**A write that cannot be recorded does not happen.**

Not "is logged as best effort". The audit row is written as part of the same
operation, and if the audit store is unreachable, writes are refused with a 503
that says so. The console presents that as intended behaviour rather than as a
malfunction.

**And the record cannot be edited.** The application connects as `tms_app`,
which has `INSERT` and `SELECT` on the audit tables and **not** `UPDATE` or
`DELETE`. That is a database grant, not application logic — code that tried to
rewrite history would fail at the connection, not at a code review.

> Verify it on your own deployment. As `tms_app`:
>
> ```sql
> UPDATE audit_action SET reason = 'x';   -- must fail
> DELETE FROM audit_action;               -- must fail
> ```
>
> If either succeeds, the grants migration was not applied, and history that
> can be edited is not history.

---

## What is recorded

| Action | When |
|---|---|
| `QUERY_KILL` | A query was killed |
| `HEALTH_TEST_TOGGLE` · `HEALTH_ROLLUP_TOGGLE` · `HEALTH_THRESHOLD_CHANGE` | A health check was disabled or retuned |
| `AUDIT_EXPORT` | Somebody exported this log |
| `CLUSTER_RESTART` | Each step of a safe restart, separately |
| `NODE_SHUTDOWN` | A worker was drained |
| `RESOURCE_GROUP_CHANGE` · `RESOURCE_GROUP_REVERT` | Admission control was edited or rolled back |
| `FLEET_JOB_RUN` | A playbook was run |
| `BENCHMARK_RUN` · `BENCHMARK_QUERY_CHANGE` · `BENCHMARK_SCHEDULE_CHANGE` | |
| `CATALOG_CHANGE` · `CATALOG_DEPLOY` | A catalog was edited in TMS, or written to a cluster |

Each row carries the actor, their roles, their IP, the target, the cluster, the
reason, a request id, and the outcome.

`AUDIT_EXPORT` being in the list is the point: reading the audit log is itself
audited.

Revert has its own action rather than being a flag on a change, so *"how often
are these undone"* stays answerable.

---

## Reading it

Filter by actor, action type, target or time range. The reason is shown in full
— it is usually the most useful column, and truncating it would defeat why it
was required.

## Exporting

Export produces CSV and **requires a reason**, which is itself recorded.

An export with no matching rows still returns the header row. A zero-byte file
is indistinguishable from a failed download, and somebody will conclude the
export is broken rather than that nothing matched.

---

## What it is not

**Not a log aggregator.** Application logs go to journald and from there to
whatever you use — Loki, OpenSearch. This table holds *actions TMS took on your
behalf*, which is a much smaller and much more answerable set.

**Not query history.** Completed queries are a different system's job. TMS
records that a query was killed, by whom and why; it does not record that a
query ran.
