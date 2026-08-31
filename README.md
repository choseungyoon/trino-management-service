# TMS — Trino Management Service

**An operations console for people who run OSS Trino.**

Trino gives you a coordinator UI that shows what is running. It does not tell
you whether a cluster can take queries, who killed something and why, whether
your workers are all configured the same way, or whether last week's memory
change made anything faster. TMS answers those, and it does so without ever
standing between a client and a query.

```
┌──────────────┐        reads          ┌─────────────────┐
│     TMS      │ ────────────────────▶ │  Trino cluster  │
│  (console)   │   REST · JMX · SSH    │  (coordinator   │
└──────────────┘                       │   + workers)    │
       │                               └─────────────────┘
       │  never on the query path              ▲
       │                                       │
   ┌───┴────┐                            ┌─────┴─────┐
   │ people │                            │  clients  │
   └────────┘                            └───────────┘
```

If TMS is down, every query still runs. That is the first rule and everything
else is arranged around it.

---

## Why this exists

Running OSS Trino past a handful of users means answering the same operational
questions over and over, and the engine does not answer them: is this cluster
healthy enough to route to, who ran the thing that broke it, why do two nodes
behave differently, did that tuning change help. Most teams end up with a
Grafana dashboard, a folder of shell scripts and a lot of tribal knowledge.

TMS is that layer, built to be run by a small platform team on plain VMs.

It assumes a shape rather than a size: **Trino on VMs with systemd**, one or
more clusters, optionally a Trino Gateway in front, and a team that would
rather approve a change than perform it twenty times. Every design decision is
written down with its reasoning in [`docs/DECISIONS.md`](docs/DECISIONS.md) —
the team's working record, kept in Korean.

---

## What it does

| | |
|---|---|
| **Live queries** | Every running query across clusters, with a kill that requires a reason — and delivers that reason to whoever owns the query |
| **Cluster health** | Eight synthetic checks that answer "can this cluster take a query right now", each carrying a written remedy |
| **Safe restart** | A six-step sequence: stop intake → drain → confirm empty → restart → verify health → restore traffic. No step is reachable out of order |
| **Fleet** | Node inventory, versions, and graceful worker shutdown that waits for tasks to finish |
| **Configuration** | What each node actually has in `etc/`, and where nodes of the same role disagree |
| **Catalogs** | Write and remove catalog files across a cluster, gated on a development cluster first |
| **Resource groups** | Edit Trino's `db` resource group tree, with validation, history and revert |
| **Benchmark** | Run query sets on a schedule, compare clusters and releases, watch a trend |
| **Gateway** | Which backends the Trino Gateway has, and how they map to clusters |
| **Audit** | Append-only record of every write, exportable, and impossible to bypass |

Screens are English, dark by default, and dense — this is a console you read
during an incident, not a dashboard you leave on a wall.

---

## What it deliberately does not do

A tool that does everything badly is worse than one that does less. TMS
delegates, and the boundaries are contractual:

| Not built | Use instead |
|---|---|
| SQL editor | Superset, DBeaver, the Trino CLI |
| Metric charts and dashboards | Grafana |
| Alerting engine | Alertmanager |
| Log collection and search | Loki, OpenSearch |
| RBAC editing UI | OPA policies in git |
| Data catalog / lineage | Out of scope |

The one chart TMS draws is of its own benchmark results, because that data
exists nowhere else.

---

## Getting started

```bash
git clone <this repository>
cd trino-management-service
python3 -m venv venv && venv/bin/pip install -e .
cp config/config.secret.yaml.example config/config.secret.yaml   # then edit
venv/bin/tms-config-check                                        # before starting
venv/bin/tms-api
```

Full instructions, including the PostgreSQL schema and the systemd units, are
in **[docs/usage/install.md](docs/usage/install.md)**.

**Want to look around first?** The demo runs everything in memory — no
PostgreSQL, no Trino, no cluster, nothing persisted:

```bash
venv/bin/python -m tests.browser.demo
```

It prints a URL and a throwaway login. Every screen is populated, the writes
really do apply (to memory), and the real validation rules really do refuse.

---

## Usage documentation

Each feature has its own page: what it is for, what it refuses to do, and what
to configure.

| | |
|---|---|
| [Install and configure](docs/usage/install.md) | Prerequisites, database, service accounts, first run |
| [Watching a cluster](docs/usage/observing.md) | Overview, live queries, health, workload |
| [Safe restart](docs/usage/safe-restart.md) | The six-step sequence and why each step exists |
| [Fleet](docs/usage/fleet.md) | Node inventory, graceful shutdown, playbook jobs |
| [Cluster configuration](docs/usage/cluster-config.md) | Reading `etc/` back, finding drift |
| [Catalogs](docs/usage/catalogs.md) | Adding and removing catalogs across a cluster |
| [Resource groups](docs/usage/resource-groups.md) | Editing the admission control tree |
| [Benchmarking](docs/usage/benchmark.md) | Query sets, comparisons, trends, schedules |
| [Gateway](docs/usage/gateway.md) | Backend visibility and routing |
| [Audit](docs/usage/audit.md) | What is recorded, and how to export it |
| [Configuration reference](docs/usage/configuration-reference.md) | Every `config.yaml` key |

---

## How it is built

Two processes and one database.

```
tms-collector ──poll──▶ Trino REST + JMX ──▶ PostgreSQL snapshots
                                                    │
tms-api ──────read──────────────────────────────────┘
   │
   ├─ serves the React console (static bundle, no Node at runtime)
   ├─ writes go straight to Trino / the Gateway, never through the collector
   └─ every write: reason required, audit row written, or the write is refused
```

| | |
|---|---|
| Backend | Python 3.9+, FastAPI, PostgreSQL |
| Frontend | React 19 + TypeScript, built with Vite, **committed to the repository** — the deployment host has no Node |
| Deployment | `pip install` + systemd. No containers required, no Kubernetes |
| Cluster operations | Ansible, run from the TMS host, with one playbook per purpose |

Design notes are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Principles

These are enforced in code and in review, not just stated.

1. **Never on the query path.** TMS reads Trino; it never proxies a query. If
   the event listener buffer fills, events are dropped rather than blocking a
   coordinator.
2. **Never render missing data as healthy.** Stale wears a badge, unknown
   outranks good, and a permission problem names the fix rather than showing an
   empty list.
3. **Every write is a ceremony.** A reason is required, the target is shown
   before you confirm, and the audit row is written or the write does not
   happen.
4. **Destructive actions cannot skip their sequence.** There is no endpoint
   that stops traffic without draining, and none that deploys and restarts in
   one act.
5. **Nothing about Trino is asserted without measuring it.** Every version-
   specific claim in this repository cites a verification in
   [`docs/TRINO_VERIFIED.md`](docs/TRINO_VERIFIED.md), most of them made against
   a real Trino 477. Several corrected an assumption that would have shipped a
   bug.

---

## Status

In production use. Interfaces are still moving; treat this as pre-1.0.

| | |
|---|---|
| Trino | Verified against **477**. Other versions are untested — see [why that is not a formality](docs/usage/install.md#a-note-on-trino-versions) |
| Trino Gateway | 19 (optional) |
| Python | 3.9 and up |
| PostgreSQL | 14 and up |

What is being worked on next is in [`docs/TODO.md`](docs/TODO.md) — again a
working document rather than user documentation, and in Korean.

---

## Contributing

Read [`CLAUDE.md`](CLAUDE.md) first — it holds the rules that a change is
reviewed against, including the two that reject the most work: *do not assert
an unverified Trino fact*, and *do not build a non-goal*.

```bash
venv/bin/pip install -e ".[dev]"
venv/bin/python -m pytest -q             # unit and API tests, no infrastructure
npm --prefix frontend run build          # ⛔ commit the output with your change

venv/bin/pip install -e ".[browser]" && venv/bin/python -m playwright install chromium
venv/bin/python -m unittest tests.browser.ui_behaviour   # what only a browser sees
```

The frontend build output is committed on purpose: the deployment host has no
Node, so anything absent from the repository is absent from the server.

---

## Licence

Not yet chosen. Until one is added, treat this as source-available for
evaluation.
