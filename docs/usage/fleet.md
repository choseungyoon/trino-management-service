# Fleet

**Every node in a cluster, with the address TMS reaches it on** — coordinators
and workers, what each is running, and whether it answered.

If you are looking for *"where is this node"*, this is the screen. The
coordinator's address is also on each [Overview](observing.md#overview) card,
because that one needs no setup.

---

## Setup

```yaml
fleet:
  enabled: true
  poll_interval_seconds: 60
  inventories:
    prod-a: /etc/tms/ansible/cluster1.ini
  node_url_template: "https://{address}:8443"
```

The inventory is an **Ansible inventory file you probably already have**. TMS
reads it and never runs it; all it needs is that coordinators and workers are
in recognisable groups:

```ini
[coordinator]
trino-coord-1 ansible_host=10.0.0.10

[workers]
trino-worker-1
trino-worker-2
```

If your groups are named differently, add aliases at the end of the file rather
than renaming anything — `docs/templates/cluster-inventory.ini.example` shows
how, and your existing playbooks keep working.

Check it before restarting anything:

```bash
venv/bin/python -c "
from tms.fleet.inventory import load_inventory
for n in load_inventory('/etc/tms/ansible/cluster1.ini','prod-a'):
    print(f'{n.role:<12} {n.host:<24} -> {n.address}')"
```

---

## Who owns the node list

Two answers, and the choice is `fleet.source`:

| | Where the list lives | Adding a worker means |
|---|---|---|
| `inventory` (default) | the files above, edited by hand | editing the file on the server |
| `tms` | TMS's own table | pressing **Scan the coordinator** |

Under `tms`, TMS asks the coordinator which nodes have joined
(`system.runtime.nodes`) and keeps the answer. It then **renders** the inventory
files itself, into `<cluster_ops.ansible.state_dir>/inventory/`. Everything that
targets hosts — [safe restart](safe-restart.md),
[configuration](cluster-config.md), [catalogs](catalogs.md), jobs — reads those
generated files, so there is one list and it is the one you can see.

```yaml
fleet:
  enabled: true
  source: tms
  node_url_template: "https://{address}:8443"
  inventories: {}          # must be empty under `tms`
cluster_ops:
  ansible:
    state_dir: /var/lib/trino-management-service
    inventories: {}        # must be empty too
```

Both maps must be empty; TMS refuses to start otherwise. A second place to
answer "which hosts are in this cluster" is what this setting exists to remove,
and a source that is merely ignored is one that eventually wins an argument
nobody knew was happening.

### Moving across

**If you already have inventory files, import them first**, while they are
still the configured ones. Discovery finds only the nodes that are *currently
answering*, and the ones that are not are exactly the entries worth keeping:

```bash
tms-import-inventory --config /etc/tms/config.yaml --dry-run
tms-import-inventory --config /etc/tms/config.yaml
```

Everything imported is marked hand-entered until the first scan confirms it —
it came from a file, not from the coordinator. Then set `source: tms`, empty
both `inventories` maps, and run `tms-config-check`.

**Starting from nothing** — no inventory files, Fleet never enabled — there is
nothing to import. Skip it. The list starts empty and **the first scan is the
setup step**: configure, start, open Fleet, press *Scan the coordinator*.

⛔ Until it has run, restarts and deployments are refused. That is deliberate.
`ansible-playbook` against an inventory with no hosts matches nothing and
**exits 0** — TMS would report a restart that never touched a machine, on a
cluster it had already drained and pulled out of rotation. So it refuses
before stopping any traffic, and `tms-config-check` warns about it beforehand.

TMS writes the (possibly empty) inventory files at startup, so everything that
needs a path finds one.

> Requires `ExecuteQuery` for the TMS account — the same grant the *identify*
> button needs. See [the catalogs guide](catalogs.md) for how that is scoped.

### ⛔ A scan never removes anything

A node that stops appearing in `system.runtime.nodes` is either decommissioned
or **down**, and TMS cannot tell which. So a scan adds and refreshes, and
reports what it did not hear from:

```
trino-a-c1   10.0.0.10   coordinator   discovered   answering
trino-a-w1   10.0.0.11   worker        discovered   answering
trino-a-w9   10.0.0.19   worker        added by sre.kim   no answer · last seen 6d ago
```

**Every row here is a deployment target, including the ones not answering.** A
worker that is down still has to come back running the same configuration as
its siblings — dropping it from the list automatically is how a node returns
from a disk swap a month behind everyone else.

Removing one is therefore a decision, with a reason, recorded in the
[audit log](audit.md). It means "stop deploying to this host".

Adding one by hand is for the same situation in reverse: the node is down, so
discovery cannot see it, and it still needs the configuration.

---

## The node list

Per node: the inventory name, the **address** TMS connects to, role, state,
Trino version, `node.environment`, and uptime.

The name and the address are shown separately when they differ. TMS connects to
`ansible_host` when the inventory sets one — an inventory alias is often a name
only Ansible resolves, and connecting to it would make a healthy node look
dead.

Above the table, TMS reports **disagreements across the fleet** — each of these
is invisible in a single row and obvious across the whole cluster, and each is a
real incident shape:

| | |
|---|---|
| Mixed Trino versions | A rolling upgrade that stalled |
| `node.environment` mismatch | This node is answering for a different cluster |
| Two nodes claiming to be coordinator | |

### "No answer"

A worker TMS cannot reach shows as **No answer**, with the error in a tooltip.
Before assuming the node is down, check:

- `node_url_template` — the port and the scheme
- the firewall between the TMS host and the worker's HTTP port
- by hand: `curl -sk https://<worker>:8443/v1/info` (no authentication needed)

---

## What this screen does not know

Stated on the screen rather than left to be inferred from a missing column — a
monitoring screen that quietly omits a fact is read as that fact being fine.

**Which node failed to join discovery.** That needs
`SELECT … FROM system.runtime.nodes`, which needs the `ExecuteQuery` permission.
TMS can say "the inventory lists 12 and the coordinator counts 11"; naming the
missing one is a separate button that runs exactly one query, offered only when
the counts already disagree.

> That button is deliberate restraint. It costs the coordinator a query slot,
> and the decision to grant `ExecuteQuery` at all was made on the basis that
> such queries stay rare.

---

## Graceful shutdown

Draining a worker out of the cluster.

The confirmation says what will happen: Trino stops accepting new tasks on that
node, finishes the ones it has, then exits. That takes **at least twice
`shutdown.grace-period`** — about four minutes on the defaults — and the node
stays listed until it goes.

**Running queries are not killed.**

> Do not decide it has hung before that time has passed. Four minutes of a
> node still being listed is the expected behaviour, not a fault.

Requirements: the account needs `WriteSystemInformation` **on the worker**, not
only on the coordinator. Trino's own documentation is explicit that access
control configuration must be present on all workers. A worker whose OPA is
down will refuse the shutdown — which is worth having on your monitoring list
as a new failure mode.

---

## Jobs

Configured playbooks, run on request, with output streamed and history kept.

```yaml
fleet:
  jobs:
    scale_out:
      playbook: /etc/tms/ansible/add-workers.yml
      title: Add workers
      description: Provisions worker VMs and joins them to the cluster.
      parameters:
        count: {label: Workers to add, min: 1, max: 10, default: 2}
```

Each run records who, why, the parameters, the exit code and every line of
output. One job at a time per cluster: two playbooks writing the same inventory
at once is not a conflict anyone can untangle afterwards.

> ⛔ **These are not restarts, and a restarting playbook must not be listed
> here.** There is no drain check on this path, so putting one here would be a
> way around the safe sequence. `tms-config-check` refuses that configuration.

TMS knows the exit code and nothing else about what the playbook did. The job
screen says so, above a green "succeeded", because somebody reading that word
should know the limit of the claim.

### When TMS restarts mid-job

The state becomes `unknown` — not failed. The playbook may well have completed;
TMS simply stopped watching. Check the nodes before running it again.
