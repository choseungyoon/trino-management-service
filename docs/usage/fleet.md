# Fleet

Every node in a cluster, what it is running, and whether it answered.

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

## The node list

Per node: role, state, Trino version, `node.environment`, and uptime.

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
