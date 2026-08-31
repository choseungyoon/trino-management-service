# Resource groups

Editing Trino's admission control tree — how many queries each group may run,
how much memory it may use, and which queries land in it.

---

## ⛔ What this screen changes

**Query admission control on a running cluster.** A value reaches every
coordinator within `resource-groups.refresh-interval` — around ten seconds —
and there is **no restart acting as a gate**.

Set a concurrency limit to something too low and queries start queueing
immediately. There is no deploy step to catch it in between.

Practise on a group that nothing uses before touching one that matters.

---

## Requirements

Trino must already be using the **`db` resource group manager**. If it is still
on `file`, this screen has nothing to edit — the file is the source of truth
and TMS does not write it.

```yaml
resource_groups:
  enabled: true
  schema: trino_resource_groups        # must match ?currentSchema= in Trino
  group_provider_configured: false
```

The `tms_app` database role needs read and write on that schema (three GRANT
statements — see `docs/runbooks/resource-groups-db.md`).

The tables live in **their own schema** inside the TMS database, so a TMS
migration has no path to the rows Trino reads to decide whether to admit a
query.

> `schema` must match `?currentSchema=` in the coordinator's
> `resource-groups.config-db-url`. If they differ, you will be editing rows
> nobody reads — and everything will look like it worked.

---

## Two screens, two different questions

| | |
|---|---|
| **Resource groups** (this one) | What is **configured**. Reads the database. Every group appears whether or not it has ever run a query |
| [**Workload**](observing.md#workload) | What is **running**. Reads JMX. Groups are created lazily, so only groups that have seen traffic appear |

This screen puts them side by side. A group's state tells you which:

| | |
|---|---|
| `running` | Configured, and its MBean is registered |
| `no traffic yet` | Configured and exported, never used |
| `not exported` | `jmxExport` is off — invisible to the workload screen **by design**. Per-user groups often skip it deliberately — one MBean per user stops being free once the user count grows |
| `unknown` | Workload collection is off, so TMS did not look |

**Groups running with no configuration behind them** get their own warning.
Either somebody edited the database by hand, or this cluster's
`node.environment` is not what TMS thinks — and the second one means everything
else on the screen describes the wrong cluster.

---

## Editing

Rows are edited in place. Not in a modal: the surrounding tree is the context
that makes a number mean something, and a dialog would cover it. One row at a
time — two open editors on one tree are two people about to overwrite each
other.

Every save needs a reason, lands an audit row (`RESOURCE_GROUP_CHANGE`), and is
validated first. A refusal keeps what you typed.

### What is refused

| | |
|---|---|
| Concurrency or queue limit of 0 | That stops the group entirely. It is a delete wearing a tuning value's clothes — Trino accepts it, TMS does not |
| A memory limit that is neither `100GB` nor `80%` | |
| `soft_cpu_limit` without `hard_cpu_limit` | Trino's documentation requires both |
| A scheduling policy outside the four Trino has | |
| A `query_priority` group with a child that is not | Trino requires the whole subtree to match |
| Two groups with the same name under one parent | The database has no unique constraint here, so duplicate trees appear silently |
| A selector pointing at a group in another environment | Nothing would ever happen |
| Removing the **last catch-all selector** | See below |

### What is warned about

Legal, but usually not what was meant:

| | |
|---|---|
| Sibling memory percentages adding to more than the parent | Percentages are of the **cluster**, not of the parent |
| `jmxExport` off on a structural group | Deliberate on per-user leaves, a mistake on a group you want to watch |
| A new CPU or scan quota | Exceeding one does not fail the query — it **waits silently** for the rest of the quota period. The hardest failure to diagnose |
| A `user_group_regex` selector with no group provider | It will never match anything |
| A group no selector points at | Unreachable, unless it is only a parent |

---

## The catch-all selector

**The last catch-all cannot be deleted.** Not discouraged — the button is not
offered, and the server refuses it too.

Every other selector narrows what it matches, so a query matching none of them
has nowhere to go, and Trino 477 does not document what it does in that case. A
configuration nobody has tested — including Trino's own authors — is not
something a console should let you create by clicking.

## `user_group_regex`

This only ever matches when a group provider is configured
(`etc/group-provider.properties`). Without one, Trino always passes an empty
group set and the selector is a dead rule.

TMS cannot see that file, so it takes your word for it via
`group_provider_configured`. Leaving it `false` is the safe answer: the editor
then warns whenever such a selector is created.

---

## Deleting a group

Both foreign keys in Trino's schema are `ON DELETE CASCADE`, so removing a group
takes **its whole subtree and every selector pointing into it**.

The confirmation lists what goes, rather than counting it. A count is something
people accept; a list is something they read. And then: *"ten seconds after
this, the coordinators are running without them."*

---

## History and revert

Trino's own tables keep no history and have nowhere to record *why* a value was
chosen, so TMS keeps that alongside them.

Every change is a revision with who, when and why. **Revert restores the whole
environment** to how it was before that change — not one field. Partial undo
multiplies the states that have to be validated, and every one of them is a way
to leave the tree in a shape nobody tested.

Reverting **appends**. It never removes what came before, and it is its own
audit action (`RESOURCE_GROUP_REVERT`) so *"how often are these undone"* stays
an answerable question.

> The revision table is append-only at the database level. If `UPDATE` or
> `DELETE` on it succeeds as `tms_app`, migration `011` was not applied — and
> history that can be edited is not history.
