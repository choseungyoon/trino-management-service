# Using TMS

One page per feature. Each says what the feature is for, **what it refuses to
do and why**, and what you have to configure to switch it on.

Most features are off by default. That is deliberate: a console that starts
with everything enabled is a console that starts by connecting to things you
did not ask it to touch.

## Start here

| | |
|---|---|
| [Install and configure](install.md) | Prerequisites, database, service accounts, first run |
| [Configuration reference](configuration-reference.md) | Every `config.yaml` key, what it does, what happens when it is wrong |

## Watching

| | |
|---|---|
| [Watching a cluster](observing.md) | Overview, live queries, health checks, workload |
| [Gateway](gateway.md) | Which backends exist and how they map to clusters |
| [Cluster configuration](cluster-config.md) | What each node actually has in `etc/`, and where nodes disagree |

## Changing

Everything below writes to a cluster. All of it requires a reason, records an
audit row, and is restricted to administrators.

| | |
|---|---|
| [Safe restart](safe-restart.md) | The six-step sequence, and why no step can be skipped |
| [Fleet](fleet.md) | The node list, graceful worker shutdown, playbook jobs |
| [Catalogs](catalogs.md) | Adding and removing catalogs across a cluster |
| [Resource groups](resource-groups.md) | Editing Trino's admission control tree |

## Measuring

| | |
|---|---|
| [Benchmarking](benchmark.md) | Query sets, cluster comparison, trends, schedules |

## Accountability

| | |
|---|---|
| [Audit](audit.md) | What is recorded, why it cannot be bypassed, how to export |
| [Work board](work-board.md) | Tracking what the platform team owes each other |

---

## Conventions used on every screen

**Freshness.** Anything read from a cluster carries when it was read. Past the
staleness threshold it says so and health drops to UNKNOWN. TMS never shows an
old reading as though it were current.

**Status vocabulary.** `GOOD` · `CONCERNING` · `BAD` · `UNKNOWN`, always with
an icon and a word — never colour alone. `UNKNOWN` outranks `GOOD`: not knowing
is never reported as fine.

**Every non-GOOD state carries a remedy.** If a check can fail, the failure
says what to do about it. A check without advice does not get merged.

**Reasons are not paperwork.** For a kill, the reason reaches the person whose
query is being killed, inside the error they see. For everything else it is
what the audit row will say six months from now.

**Off is not broken.** A feature you have not configured says so and explains
what to configure. It does not 404 and it does not show an empty table that
looks like "nothing is happening".


---

## Beyond these pages

These are the user-facing documents and they are the ones kept current for
readers outside the project.

The repository also carries the team's own working record — decisions and their
reasoning, requirements, verified Trino behaviour, and the list of what a person
still has to do. Those are **written in Korean** and assume context this
documentation does not. They are worth reading for *why* something behaves the
way it does; `docs/TRINO_VERIFIED.md` in particular records measurements against
a real Trino, several of which contradicted the documentation.
