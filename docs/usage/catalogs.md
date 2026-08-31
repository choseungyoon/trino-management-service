# Catalogs

Writing and removing `etc/catalog/*.properties` across a cluster, from the
console, instead of editing the same file once per node.

---

## ⛔ Read this before using it

A catalog file Trino cannot load **stops the whole server from starting**. Not
"that catalog is missing and the rest works" — the process exits.

Measured against Trino 477, three separate mistakes, all with the same result:

| Mistake | What the log says |
|---|---|
| Connector name that does not exist | `No factory for connector 'x'` |
| Property the connector does not have | `Configuration property 'x.y' was not used` |
| `${ENV:VAR}` whose variable is not set on the node | `Environment variable is not set: VAR` |

Deploy one of those to every node and restart, and none of them come back.

**And TMS cannot check any of it in advance.** The list of valid connectors
appears only inside the exception of a server that has already failed; there is
no machine-readable list of each connector's properties anywhere. (The `plugin/`
directory names are not connector names — `delta-lake` on disk is `delta_lake`
in a catalog file.)

So the validator is not code. It is a cluster.

---

## The development cluster gate

**A catalog must be deployed to a development cluster and survive a restart
there before it can go anywhere else.**

```yaml
cluster_ops:
  config_scan:
    development_clusters: [dev-a]     # shared with the config scan
  catalog_deploy:
    playbook: /etc/tms/ansible/deploy-catalog.yml
```

> ⛔ With no development cluster listed, TMS **refuses to start**. A gate that
> is configured but has nowhere to run is worse than no gate, because people
> believe it is there.

On the screen, each catalog row has one button per cluster. Buttons for
clusters it has not earned are disabled, and the tooltip says why — a greyed
button that cannot explain itself is a button people file tickets about.

Editing a draft **clears the proof**. Otherwise somebody proves a working
catalog on the development cluster, changes a property, and ships the change on
the strength of a test that never saw it. Editing only the notes keeps it:
re-proving for a comment would teach people to skip the gate.

---

## Deploying does not restart

The file lands on every node and **nothing reads it**. Trino reads static
catalogs only at startup.

That is deliberate. Restarting belongs to the [safe restart
sequence](safe-restart.md), which stops intake and drains first. A deploy that
restarted by itself would be the path around that.

So a deploy leaves the cluster in a state the screen names explicitly: the file
is there, and it does nothing until you run a restart — which is where a bad
catalog will fail, loudly, on the cluster you chose to fail on.

---

## Credentials

TMS does not store them. A credential-shaped property must be an environment
reference:

```properties
connector.name=postgresql
connection-url=jdbc:postgresql://db:5432/reporting
connection-user=trino
connection-password=${ENV:PG_REPORTING_PASSWORD}
```

A literal is refused — when the draft is written, **and again immediately
before it is deployed**. The second check is the one that matters: a row can be
edited between the two, and that check is what stands between a plaintext
password and every node in the cluster.

Trino resolves the reference from the node's own process environment, which
means:

> ⛔ **The variable must already exist on every node you deploy to.** A
> reference whose variable is missing stops the server from starting, exactly
> like a bad connector name. The screen lists which variables a catalog needs,
> under "Needs", so you can check before deploying rather than during a
> restart.

---

## A first run, end to end

1. **New catalog** → name `probe_memory`, connector `memory`, no properties.
   Harmless on purpose.
2. Deploy to the development cluster. The file appears on its nodes.
3. Confirm nothing happened yet — `SHOW CATALOGS` does not list it.
4. Run a safe restart of the development cluster.
5. `SHOW CATALOGS` now lists it, and the production buttons have unlocked.

Then, once, do it wrong **on the development cluster**: deploy a catalog with a
connector name that does not exist and restart. Watching the coordinator fail
to come back is how the gate stops being a rule and starts being a reflex. To
recover: **Show** → **Remove from a cluster**, then restart again.

---

## Removing

Removing needs no proof. It cannot be validated by proving it somewhere else,
and refusing to remove a catalog is its own hazard.

Removing a **draft** from TMS does not touch any cluster. A catalog already
deployed stays where it is until it is removed from that cluster.

> Removing a catalog that Hive, Iceberg, Delta Lake or Hudi were using does not
> free the connector's resources until a restart. Since a removal needs a
> restart anyway, this mostly matters as a reason not to expect the memory back
> before then.

---

## What is recorded

Every deploy and removal writes a row that keeps the properties **by value** —
the draft can be edited afterwards, and *"what did we actually put on prod-a
last Tuesday"* has to stay answerable.

Failures are kept too. The row for a catalog that stopped a cluster from
booting is the one somebody will need most, and the application's database role
cannot delete it.

Audit actions: `CATALOG_CHANGE` for edits in TMS, `CATALOG_DEPLOY` for anything
that reaches a cluster.

---

## Why not `CREATE CATALOG`?

Trino 477 has dynamic catalog management, and TMS does not use it:

| | |
|---|---|
| It is **experimental** | The syntax may change incompatibly, and it has security implications the documentation states outright |
| It needs `catalog.management=dynamic` | A cluster-wide change, away from the default |
| **The whole query is logged** | Including the password, visible in the Web UI. That is the opposite of what `${ENV:VAR}` achieves |
| `ALTER CATALOG` does not exist | Changing a catalog is drop-and-create, which for Hive and Iceberg needs a restart anyway |

File deployment works with the defaults you already run, and keeps credentials
out of both TMS and the query log.
