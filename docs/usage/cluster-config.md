# Cluster configuration

What each node actually has in its `etc/` directory, and **where nodes of the
same role disagree**.

This screen only reads. There is no task in the collection playbook that
changes anything, which is a property you can confirm by reading one short
file: `docs/templates/collect-config.yml`.

---

## Why it exists

Somebody fixed one worker by hand a year ago and never wrote it down. Nothing
tells you which one, and the symptom is a query that is mysteriously slower
whenever it lands on that node.

The more nodes you have, the less likely you are to find it by looking.

---

## Setup

Requires SSH from the TMS host to every node — the same access that
`cluster_ops.restart_mode: ansible` uses, and the same inventories.

1. Copy `docs/templates/collect-config.yml` to `/etc/tms/ansible/`
2. Adjust `trino_etc` and `trino_log` at the top of the file to your paths
3. Point the config at it:

```yaml
cluster_ops:
  config_scan:
    playbook: /etc/tms/ansible/collect-config.yml
    development_clusters: [dev-a]
```

Try it by hand first. You should get one line per host:

```bash
ansible-playbook -i /etc/tms/ansible/cluster1.ini collect-config.yml | grep TMS-CONFIG-SCAN
```

---

## Scanning

**On request, never on a timer.** A scan opens an SSH connection to every node
in the cluster; doing that every thirty seconds would make TMS the noisiest
thing on the fleet, for an answer that only changes when somebody changes it.

**Administrators only.** It is a read, but a read that connects to every node.

---

## Reading the result

### Compared within a role, never across

This is the judgement the whole screen rests on. A coordinator and a worker are
*supposed* to differ — `coordinator=true`, a different heap, sometimes a
different port. Comparing them against each other would report drift on every
healthy cluster, and a drift screen that is always red is a drift screen nobody
reads.

So: coordinators are compared with coordinators, workers with workers.

### Three kinds of finding

| | |
|---|---|
| **Different value** | Nodes of the same role are running different values for the same setting |
| **Different file** | The file's checksum differs |
| **File missing** | Some nodes of the role have it, others do not |

### Expected differences

`etc/node.properties` is listed separately. It holds `node.id`, which **must**
be unique per node, so it differs on every healthy cluster. It is shown because
"which nodes have it" is still worth seeing, and marked so the screen does not
shout about it.

> For the same reason, TMS has no feature that deploys `node.properties` as one
> file. There is no such thing as "the right content" for it — only "the right
> content for this host".

### Credentials do not travel

| | |
|---|---|
| `etc/config.properties`, `jvm.config`, `log.properties` | Content collected |
| `etc/catalog/*.properties` | **Checksum only** |
| Any value whose key reads like a credential | Dropped before it reaches TMS |

A catalog file holds `connection-password`. Copying those into TMS's database
would move the credentials with them, so "do these nodes have the same catalog"
is answered without "what is in it".

The redaction pattern errs toward hiding: `http-server.https.keystore.key` is a
password despite reading like a filename. A value wrongly hidden costs a screen
cell; a value wrongly shown costs a credential.

---

## Known properties

Each node reports every configuration property its Trino accepts — around 450,
taken from its own startup log.

**TMS keeps no list of its own**, and that is a deliberate refusal rather than
a missing feature. An unrecognised property name stops a Trino server from
booting, so the check matters; but a hand-written table would be a second
opinion about a build TMS has never seen, and it would be wrong after every
upgrade. Asking the cluster is correct by construction and follows version
differences automatically.

The number shown is the **intersection** across scanned nodes, not the union: a
deploy goes to several nodes at once, so a property only one node knows is a
property that would stop the others booting.

> If this column is empty, `trino_log` is pointing at the wrong file or the log
> has rotated past the last startup. Fix that — this list is what a deploy
> checks a typo against, and **nothing deploys until it is populated**.

---

## Development clusters

```yaml
development_clusters: [dev-a]
```

A cluster listed here does not report an unreachable node as drift. Its worker
count changes with whatever is being tested, so "a node did not answer" is
normal there and alarming everywhere else.

It also marks which cluster a catalog or a configuration change must be proved
on before it can go anywhere else — see [Catalogs](catalogs.md) and below.

---

## Changing config.properties

```yaml
cluster_ops:
  config_deploy:
    playbook: /etc/tms/ansible/deploy-config.yml
```

Off unless that is set, and it needs `config_scan` on as well — TMS refuses to
start otherwise, because the scan is where the typo check gets its material.

### A change is a set of edits, not a file

One property per line, as it will appear in the file. A line starting with `-`
removes that property instead:

```
query.max-memory=1200GB
-node-scheduler.include-coordinator
```

⛔ **Lines TMS was not given are left exactly as they are.** This is not a
nicety — the scan redacts credential-shaped values, so TMS's copy of your
`config.properties` has the literal string `[REDACTED]` where the keystore
password is. Writing that copy back would replace a working password with that
string on every node at once. So TMS sends edits and the playbook merges them.

Each node keeps a `config.properties.tms-previous` beside the real file, so
there is something to restore by hand if a change goes wrong.

### Two gates, and neither replaces the other

| | Catches | Where it runs |
|---|---|---|
| **Name check** | A misspelt property name | Everywhere, including development |
| **Development cluster** | A correct name whose *value* stops the server | Before production |

`node-scheduler.include-coordinatr` never reaches a node — the first gate has
the list of names the cluster itself reported and that name is not on it.
`node-scheduler.include-coordinator=fasle` passes the first gate and is caught
by the second, when the development cluster does not come back.

> Deploy → **restart** → confirm health is GOOD. Only then does the production
> button turn on. Editing the change afterwards clears that mark: a change
> edited after it was proved is a different change, and the test it passed
> never saw this version.

### Where it goes

`every node` / `the coordinator only` / `the workers only`. Three words, and
they are what Ansible receives as the host pattern — **no host name ever
reaches the command line**, so mis-targeting is impossible rather than
unlikely.

A coordinator-only setting going to workers gets a note on screen and is **not
blocked**: measured on Trino 477, a coordinator property on a worker starts
fine. The note is derived from what your cluster is doing today, not from a
table TMS maintains.

### ⛔ Deploying does not restart anything

Trino reads `config.properties` at startup. When the deploy finishes, the file
has changed and the cluster is **still running the old values**. Use
[Safe restart](safe-restart.md), which stops traffic and drains first.

A deploy that restarted on its own would be that sequence skipped, with a
different label on it.

### Credentials

A key that reads like a credential — `password`, `secret`, `keystore`,
anything ending in `.key` — must carry `${ENV:VARIABLE_NAME}`. A literal is
refused when the change is saved and **again immediately before it deploys**,
because a row can be edited between the two and the second check is the one
standing between a plaintext password and every node.
