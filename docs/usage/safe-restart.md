# Safe restart

Restarting a Trino cluster without killing the queries on it. Six steps, in
order, and **no step is reachable except through the one before it**.

```
1. Stop new queries reaching it, in the Gateway
2. Wait for every running query to finish
3. Confirm the cluster is empty
4. Restart it
5. Verify health is GOOD
6. Put it back in rotation
```

The sequence is the feature. Anyone can restart a cluster; the value here is
that traffic stops first, queries drain, and traffic does not come back until
health does.

---

## Requirements

**The Trino Gateway must be configured** (`gateway.enabled`). Without it there
is no way to stop traffic to a cluster, and a "restart" that skipped that step
would kill every query running on it. When the Gateway is off, the screen says
the feature is unavailable and why — it does not offer a button that would do
something unsafe.

---

## Running one

Pick the cluster, type why, press **Begin the restart sequence**. Submitting
stops traffic immediately; queries already running keep going.

From there the screen shows where the sequence has got to, what it is waiting
for, and the two numbers every gate is decided on — running + queued queries,
and the health state.

### While it drains

TMS waits. If a query is stuck, there is a **Skip the drain** control behind a
disclosure, and it needs its own separate reason:

> This overrides a check rather than following one, and the record has to say
> which it was. The running queries **will be killed** by the restart, and the
> log line says `FORCED past the drain with N queries`.

### Who restarts

Two modes, set by `cluster_ops.restart_mode`:

| | |
|---|---|
| `manual` (default) | TMS holds the gates; **you** restart the cluster and press "Done". The screen says so in the first person — "I will restart prod-a myself" — because a button reading "Restart X now" gets pressed and then nothing happens |
| `ansible` | TMS runs a configured playbook and streams its output |

**The gates are identical in both modes.** What prevents the incident is the
empty-cluster check and the health check, not who presses the button.

> ⛔ Turning on `ansible` gives the TMS host SSH access to every Trino node.
> That is a security decision, not a convenience — make it deliberately. It is
> written up in [`DECISIONS.md`](../DECISIONS.md) D-009, along with every
> constraint the code puts around that access.

### One check about coming back

Before step 4, if `resource_groups.enabled` is on, TMS asks whether the cluster
could *start again* — a Trino 477 coordinator using the `db` resource group
manager **exits at startup** if it cannot reach that store. It does not come up
degraded.

If the answer is no, the restart button refuses. Stopping a cluster whose
traffic is already blocked, with nothing able to bring it back, is the worst
outcome this sequence can produce.

### Restoring traffic

Step 6 refuses until health is GOOD. There is no override for that one.

---

## Aborting

**Abort restores traffic.** It is "put it back", not "stop".

A sequence abandoned without aborting leaves a cluster receiving nothing — a
quiet outage, because every other cluster is green and the console looks fine.
That is exactly why an active restart draws a banner on **every** screen until
it finishes.

If the Gateway cannot be reached when you abort, the sequence stays in
`ABORTING` rather than disappearing: it is still holding traffic back, and it
must stay visible until somebody fixes it. The message names the fix
(reactivate the backend in the Gateway, then abort again).

---

## What it never does

| | |
|---|---|
| **No standalone "stop intake" toggle** | Blocking traffic is reachable only as step 1. A separate toggle would be the way around the drain that follows it |
| **No deploy-and-restart in one act** | Deploying a catalog or a config change writes files and stops. Restarting is this sequence, separately |
| **No claimed success it did not see** | If TMS restarts while a playbook is running, the status is `unknown` — not success, not failure. Restoring traffic on a guess is what this exists to prevent |
| **No replay** | Each step is its own request against a known state. A stale browser tab cannot fire step 4 at a cluster that has moved on |

---

## Reading the log afterwards

Every step, every observation and every playbook line is in the progress
console, with timestamps. It is also in the audit log as `CLUSTER_RESTART`
rows, one per step, each with the actor and the reason.

The four questions the header answers, because they are the ones asked about a
restart that is taking too long: when it started, how long it has been, when it
finished, and who ran it.
