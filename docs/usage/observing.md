# Watching a cluster

The read-only screens: Overview, Live Queries, Health and Workload. None of
them can change anything, and none of them are on the query path.

---

## Overview

One card per cluster, and the question it answers is *"where do I look first"*.

Each card shows active workers against expected, running and queued counts, the
5-minute failure rate, and the roll-up health state. The health test chips
underneath are the individual checks — click one to land on it.

> **Active workers counts workers, not nodes.** Trino's `ActiveNodeCount`
> includes the coordinator, so a cluster of N workers reports N+1. TMS corrects
> for this. If you compare against the coordinator UI, that is why the numbers
> differ by one.

---

## Live queries

Everything running, per cluster, refreshed every five seconds.

Filter chips narrow to running, queued, or long-running. "Long-running" is a
threshold you set (`health.long_running_query_seconds`, default 300s), not a
judgement — it is there so a slow query is visible without reading timings.

### Killing a query

The kill dialog is a ceremony, and each part of it is load-bearing:

| | |
|---|---|
| The target card | Repeats the query id, the user, the cluster and how long it has been running. You always see what you are about to destroy |
| The reason | **Required.** Not paperwork — it is delivered to the query's owner inside the error they receive |
| The hint | Says so, explicitly, because an operator typing "test" needs to know who reads it |

The message the owner sees is:

```
Killed by TMS. actor=<you>, reason=<what you typed>, request_id=<uuid>
```

The `request_id` is also on the audit row, so a support question six months
later can be traced from the user's screenshot back to who did it.

> **Kills are never retried.** Reads are retried on failure; writes are not. A
> retried kill can kill twice — a second query may have taken the same slot.

### When the list is empty

An empty query list can mean two very different things: nothing is running, or
the service account cannot see queries. With `file` access control, a
permission denial arrives as an empty array.

Health check **H-09** compares the list against
`QueryManager.RunningQueries` from JMX and reports UNKNOWN — with advice
naming the permission — when they disagree. The screen renders that
differently from "no queries running".

---

## Health

Eight synthetic checks per cluster. Each one answers a piece of *"can this
cluster take a query right now"*, and each one carries a written remedy for
every non-GOOD state.

| | Check | Source |
|---|---|---|
| H-01 | Coordinator responsiveness | `/v1/info` — works regardless of access control |
| H-02 | Startup complete | `/v1/info` |
| H-03 | Worker registration | JMX |
| H-04 | Heap usage | JMX |
| H-05 | Query failure rate (5m) | JMX |
| H-06 | Internal (system) failures | JMX |
| H-07 | OOM kills | JMX |
| H-08 | Gateway backend registration | Gateway API — only when the Gateway is enabled |
| H-09 | Permission self-check | Query list vs JMX |

### Reading it

**A state change is confirmed over several polls.** A single bad reading does
not flip the state; the default is three stable polls
(`health.stabilization_polls`). This is why the state transition log is worth
reading — what lands there has already survived being a spike.

**H-05 and H-06 belong together.** A high failure rate with no internal
failures is users writing bad SQL. The same rate *with* internal failures is
the engine. The advice on H-05 says so rather than making you remember.

**UNKNOWN is not a failure.** It means TMS could not read the thing. Every
UNKNOWN names what would let it read — usually a `rules.json` entry.

### Tuning and turning off

Administrators can adjust a threshold or disable a test. Both are writes:
reason required, audit row written. Disabling a test also removes it from the
roll-up, and the screen says which tests are contributing so a green roll-up
cannot hide a switched-off check.

> **H-08 disappears when the Gateway is off** rather than sitting permanently
> UNKNOWN. A check that can never pass teaches people to ignore the colour.

---

## Workload

The resource group tree as Trino is running it: which groups exist, what each
one is running and queueing, and which one is holding queries back.

**Off by default** (`workload.enabled`). Collection costs one JMX registry
enumeration plus one read per exported group, on every poll. Turn it on after
you have measured the load — `scripts/measure_production_load.py` does that,
and the reason to measure first is that the existing figure was taken on a
laptop against an idle node, which the measurement itself records as a lower
bound.

### Two things this screen cannot tell you

**Groups are created lazily.** A group that has never admitted a query has no
MBean, so it is not here. This screen shows *groups that have seen traffic*,
never *groups that are configured*. It says so rather than implying a complete
inventory — for the configured set, see [Resource groups](resource-groups.md).

**`jmxExport` is invisible.** A group configured without `"jmxExport": true`
looks exactly like a group with no traffic. TMS cannot tell them apart, and
neither can you from this screen; check the configuration.

### The bottleneck column

A group is only flagged when queries are actually waiting. A group sitting at
its concurrency limit with an empty queue is working exactly as configured, and
flagging it would train you to ignore the one screen that explains a slow
cluster. The reasons, worst first:

| | |
|---|---|
| Queue full | New queries are being **rejected** |
| At concurrency limit | Queries are queueing behind the limit |
| At memory limit | |
| At CPU limit | |
