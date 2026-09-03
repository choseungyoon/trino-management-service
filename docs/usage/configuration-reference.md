# Configuration reference

Two files:

| | |
|---|---|
| `config/config.yaml` | Everything that is not a secret. Safe to commit |
| `config/config.secret.yaml` | Passwords and the session secret. **gitignored** |

Environment variables override both: `TMS_TRINO_PASSWORD`,
`TMS_GATEWAY_PASSWORD`, `TMS_DATABASE_URL`, `TMS_CONFIG`.

Run **`tms-config-check`** after any change. It validates the file, the schema
and the paths, and names what is wrong.

---

## `clusters`

The clusters TMS knows about. There is no UI for this list — see
[Gateway](gateway.md#known-limits) for why.

```yaml
clusters:
  - name: prod-a
    coordinator_url: https://coordinator-a.internal:8443
    expected_workers: 12
    trino_ui_url: https://coordinator-a.internal:8443/ui/   # optional deep link
    node_environment: cluster1                              # see below
```

| | |
|---|---|
| `expected_workers` | What health check H-03 compares against. **Workers, not nodes** — TMS already accounts for the coordinator being included in Trino's count |
| `node_environment` | This cluster's `node.environment`, copied from the **coordinator's** `node.properties`. Only needed when `resource_groups.enabled` is on; it is how TMS scopes the resource group rows and asks "could this cluster start again" before a restart |

---

## `trino`

```yaml
trino:
  user: tms-svc
  verify_tls: true
  connect_timeout_seconds: 2
  read_timeout_seconds: 5
  write_timeout_seconds: 10
  read_retries: 2
  circuit_breaker_failures: 5
  circuit_breaker_reset_seconds: 30
```

> **Reads are retried, writes never are.** A retried kill can kill twice — a
> second query may have taken the same slot.

TMS never sends `X-Trino-User`. Keeping the authenticated user equal to the
session user avoids the impersonation check entirely.

---

## `database`

```yaml
database:
  url: postgresql://tms_app@db.internal:5432/tms
```

Connect as **`tms_app`**, not the owner. That role cannot delete audit rows,
which is what makes the audit log a record rather than a suggestion.

---

## `collector`

```yaml
collector:
  query_poll_interval_seconds: 5
  jmx_poll_interval_seconds: 15
  info_poll_interval_seconds: 30
  stale_threshold_seconds: 30
  query_text_max_bytes: 4096
  response_backoff_bytes: 5000000
  response_backoff_interval_seconds: 10
```

| | |
|---|---|
| `stale_threshold_seconds` | Past this, a snapshot is shown as stale and **every health test drops to UNKNOWN**. Never show an old reading as current |
| `query_text_max_bytes` | SQL stored in the list snapshot is truncated here; full text is fetched on demand |
| `response_backoff_*` | A running query costs roughly 3.5 KB in the snapshot. Rather than guess a peak concurrency, the collector backs off when a response exceeds this |

---

## `gateway`

See [Gateway](gateway.md).

```yaml
gateway:
  enabled: false
  base_url: ""
  user: ""
  poll_interval_seconds: 30
```

Required for [safe restart](safe-restart.md) — without it there is no way to
stop traffic to a cluster.

---

## `workload`

The resource group tree from JMX. See [Watching a cluster](observing.md#workload).

```yaml
workload:
  enabled: false
  poll_interval_seconds: 15
```

Off by default: collection costs one JMX enumeration plus one read per exported
group, every poll. Measure first with `scripts/measure_production_load.py`.

Also needs `"jmxExport": true` on each group you want to see.

---

## `resource_groups`

Trino's `db` resource group manager tables. See [Resource groups](resource-groups.md).

```yaml
resource_groups:
  enabled: false
  schema: trino_resource_groups
  group_provider_configured: false
```

**Not the same thing as `workload`,** and the two are independent:

| | |
|---|---|
| `workload` | Reads JMX → what each group is **doing right now** |
| `resource_groups` | Reads the database → what groups are **configured at all** |

`schema` must match `?currentSchema=` in the coordinator's
`resource-groups.config-db-url`.

> Leave this off while Trino still uses the `file` manager. **Off means TMS
> makes no claim** — it does not mean the store was checked and found healthy.

---

## `benchmark`

See [Benchmarking](benchmark.md).

```yaml
benchmark:
  enabled: false
  default_repetitions: 3
  max_repetitions: 20
  timeout_seconds: 600
  pause_seconds: 1
```

Needs `ExecuteQuery` on the Trino account. Query sets live in the database and
are edited from the console; there is no `query_sets` key, and leaving one in
the file **refuses startup** rather than being ignored — two sources of truth
where the screen quietly wins is worse than an error.

---

## `fleet`

See [Fleet](fleet.md).

```yaml
fleet:
  enabled: false
  poll_interval_seconds: 60
  inventories:
    prod-a: /etc/tms/ansible/cluster1.ini
  node_url_template: "https://{address}:8443"
  shutdown_timeout_seconds: 900
  jobs: {}
```

| | |
|---|---|
| `inventories` | Usually the same files as `cluster_ops.ansible.inventories`. Separate so that *seeing* the fleet does not require permission to *restart* it |
| `node_url_template` | **Required when enabled.** An inventory carries addresses, not schemes or ports; guessing would make every node look unreachable, which reads as an outage rather than a typo |
| `shutdown_timeout_seconds` | A worker needs at least `2 × shutdown.grace-period` plus its running tasks. A shorter deadline times out on a perfectly healthy shutdown |
| `jobs` | Playbooks TMS may run. Parameters are **integers with bounds only** — a string parameter would hand the request a way onto the command line |

> ⛔ Never point a job at a playbook that restarts anything. There is no drain
> check on that path. `tms-config-check` refuses it.

---

## `cluster_ops`

```yaml
cluster_ops:
  restart_mode: manual          # or: ansible
  drain_timeout_seconds: 900

  ansible:
    playbook: /etc/tms/ansible/restart-cluster.yml
    binary: ansible-playbook
    timeout_seconds: 1800
    state_dir: /var/lib/trino-management-service
    inventories:
      prod-a: /etc/tms/ansible/cluster1.ini
    extra_vars: {}

  config_scan:                                     # cluster-config screen
    playbook: /etc/tms/ansible/collect-config.yml
    timeout_seconds: 600
    development_clusters: [dev-a]

  catalog_deploy:                                  # catalogs screen
    playbook: /etc/tms/ansible/deploy-catalog.yml
    timeout_seconds: 900

  config_deploy:                                   # cluster-config screen
    playbook: /etc/tms/ansible/deploy-config.yml
    timeout_seconds: 900
```

### Four playbooks, four purposes

They must be **four different files**, and TMS refuses to start if two of them
point at the same path.

| | |
|---|---|
| `ansible.playbook` | **Restarts.** Driven by the safe sequence, which drains first |
| `config_scan.playbook` | **Reads only.** No task in it changes a node |
| `catalog_deploy.playbook` | **Writes a catalog file.** Does not restart |
| `config_deploy.playbook` | **Merges edits into config.properties.** Does not restart |

Keeping them apart is what lets you confirm which is which by reading one short
file. Templates for the last three are in `docs/templates/`.

> `config_deploy` also requires `config_scan` to be on, and TMS refuses to
> start otherwise. The scan is where the list of valid property names comes
> from, and without it TMS cannot tell a typo from a real property — a typo
> stops Trino from starting.

### `restart_mode: ansible`

> ⛔ This gives the TMS host **SSH access to every Trino node**. A security
> decision, not a convenience. The constraints the code puts around that access
> are in [`DECISIONS.md`](../DECISIONS.md) D-009.

`state_dir` must match `StateDirectory=` in the systemd unit. ansible-core
refuses to run without a writable `HOME`, and the unit sets `ProtectHome=true`.
They disagree silently: restarts just block.

### `development_clusters`

Shared by the config scan and catalog deployment:

| | |
|---|---|
| Config scan | A node that did not answer is not reported as drift there |
| Catalogs | Where a catalog must be proved before it can go anywhere else |
| Config changes | Same — and the same reason: a bad value stops every node it reaches |

> With `catalog_deploy` or `config_deploy` on and this list empty, **TMS
> refuses to start**. A gate with nowhere to run is worse than no gate,
> because people believe it is there.

---

## `health`

```yaml
health:
  stabilization_polls: 3
  long_running_query_seconds: 300
  thresholds:
    heap_used_pct_concerning: 80
    heap_used_pct_bad: 90
    failure_rate_pct_concerning: 5
    failure_rate_pct_bad: 20
    internal_failures_concerning: 1
    internal_failures_bad: 5
    oom_kills_concerning: 1
    oom_kills_bad: 3
    missing_workers_pct_bad: 20
```

`stabilization_polls` is how many consecutive readings confirm a state change.
Single spikes never land in the transition log.

Thresholds can also be changed from the console — that is a write, with a
reason and an audit row.

---

## `deeplinks`

Links out to the tools TMS deliberately does not replace.

```yaml
deeplinks:
  log:
    template: ""              # e.g. https://loki/…{cluster}…{from}…{to}
    padding_seconds: 300
  query_history:
    query_url_template: ""
    home_url: ""
  grafana:
    cluster_dashboard: ""
  superset_url: ""
```

> **An empty entry renders no link at all** — deliberately, rather than a
> broken one. A dead link in an operations console is worse than an absent
> feature.

---

## `portal`

```yaml
portal:
  session_idle_timeout_minutes: 30
  session_absolute_timeout_hours: 12
  session_secret: "…"                # config.secret.yaml
  local_users:                       # config.secret.yaml
    alice:
      password_hash: "…"             # scripts/hash_password.py
      roles: [admin]
      must_change_password: true
```

> `session_secret` must be **stable across restarts and across replicas**. A
> generated one breaks every session on every restart, silently.

Local accounts are a temporary mode until directory integration exists. Roles:
`viewer`, `operator`, `admin`.

---

## `server`

```yaml
server:
  host: 127.0.0.1
  port: 8500
```

Bind to localhost and put TLS in front. The session cookie is `Secure`, so a
browser will not store it over plain HTTP.
