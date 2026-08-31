# Install and configure

TMS is two Python processes and one PostgreSQL database. There is no container
image, no Kubernetes and no Node runtime — the deployment unit is
`pip install` plus `systemctl restart`.

---

## Before you start

| | |
|---|---|
| Python | 3.9 or newer |
| PostgreSQL | 14 or newer, reachable from the TMS host |
| Trino | 477. Other versions are unverified — see [why that matters](#a-note-on-trino-versions) |
| Network | TMS host → each coordinator's HTTPS port. Optionally → each worker, and → the Trino Gateway |

You also need a **Trino service account**. TMS reads through it and never
impersonates anyone.

---

## 1. Install

```bash
git clone <this repository> /etc/trino-management-service
cd /etc/trino-management-service
python3 -m venv venv
venv/bin/pip install -e .
```

Four commands appear in `venv/bin/`:

| | |
|---|---|
| `tms-api` | The console and the JSON API |
| `tms-collector` | The polling loop that fills the snapshot tables |
| `tms-config-check` | Validates configuration and schema **before** you start anything |
| `tms-work-export` | Writes the work board to a markdown file |

> Console scripts are created at install time. `git pull` alone does not
> create a newly added command, even with `-e`.

---

## 2. Create the database

```bash
psql -U postgres -c "CREATE DATABASE tms"
psql -U postgres -d tms -c "CREATE ROLE tms_owner LOGIN PASSWORD '...'"
psql -U postgres -d tms -c "CREATE ROLE tms_app   LOGIN PASSWORD '...'"
```

Two roles on purpose. `tms_owner` owns the schema and applies migrations;
`tms_app` is what the application connects as and it **cannot** delete audit
rows. That separation is the audit guarantee — see [Audit](audit.md).

Apply every migration **in numeric order**, as `tms_owner`:

```bash
for f in migrations/*.sql; do
  echo "-- $f"
  psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f "$f"
done
```

> ⛔ **Never re-run an earlier migration after a later one.** Several of them
> drop and recreate the audit action constraint, listing every action that
> existed when they were written. Running an old one afterwards silently
> removes the newer actions, and the writes that need them start failing with
> "audit unavailable" — a feature that stops working rather than an error you
> can see. `tms-config-check` catches this by name.

---

## 3. Configure

Two files:

| | |
|---|---|
| `config/config.yaml` | Everything that is not a secret. Safe to commit |
| `config/config.secret.yaml` | Passwords and the session secret. **gitignored** |

Start from the examples and see the [configuration reference](configuration-reference.md)
for every key. The minimum is:

```yaml
clusters:
  - name: prod-a
    coordinator_url: https://coordinator-a.internal:8443
    expected_workers: 12

trino:
  user: tms-svc

database:
  url: postgresql://tms_app@db.internal:5432/tms

portal:
  local_users:
    alice:
      password_hash: "..."      # scripts/hash_password.py
      roles: [admin]
```

Secrets can come from `config.secret.yaml` or from the environment
(`TMS_TRINO_PASSWORD`, `TMS_GATEWAY_PASSWORD`, `TMS_DATABASE_URL`). The
environment wins.

### What the Trino account needs

| Permission | For | Without it |
|---|---|---|
| `PUBLIC` (`/v1/info`) | Health checks H-01, H-02 | Nothing works |
| `MANAGEMENT_READ` (`/v1/jmx/mbean`) | Health checks H-03…H-07, workload | Those checks are UNKNOWN, with advice naming `rules.json` |
| Query view + kill | Live queries, kill | The query list comes back **empty rather than forbidden** — TMS detects this and says so rather than showing "no queries" |
| `ExecuteQuery` | Benchmarks, naming an unjoined worker | Benchmarks cannot run. Grant it deliberately, not by default |

> The empty-list behaviour is not a hypothetical. With `file` access control a
> permission denial on the query list arrives as an empty array, which is
> indistinguishable from an idle cluster. Health check H-09 exists solely to
> tell those two apart.

---

## 4. Check before starting

```bash
venv/bin/tms-config-check
```

This is not a formality. It verifies the schema, the audit action list, the
inventories, the playbook paths and the cluster list, and it names what is
wrong. Every check in it exists because something failed at a worse moment.

---

## 5. Run

For a quick look:

```bash
venv/bin/tms-api        # console + API on 127.0.0.1:8500
venv/bin/tms-collector  # in another shell
```

For real, use the units in `ops/systemd/`:

```bash
cp ops/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tms-collector tms-api
```

> The units set `ProtectHome=true`. If you later switch restarts to Ansible,
> note that ansible-core refuses to run without a writable `HOME` — the unit's
> `StateDirectory=` is what provides one, and its name must match
> `cluster_ops.ansible.state_dir`. They disagree silently: restarts just block.

Put a TLS terminator in front. The session cookie is `Secure`, so a browser
will not store it over plain HTTP, and you will loop on the login page.

---

## 6. Sign in

Local accounts are a **temporary mode** until directory integration exists.
Create one with:

```bash
venv/bin/python scripts/hash_password.py
```

Put the hash in `config.secret.yaml` under `portal.local_users`. Three roles:

| Role | Can |
|---|---|
| `viewer` | Read every screen |
| `operator` | Kill queries |
| `admin` | Everything: restarts, deployments, resource groups, benchmarks |

> After a first sign-in with a temporary password, the console forces a change
> — and then shows you the new hash, because TMS cannot rewrite a config file
> it does not own. **Copy it into `config.secret.yaml` or the change is lost on
> restart.**

---

## Trying it without a cluster

```bash
venv/bin/python -m tests.browser.demo
```

No extra dependencies — it needs `uvicorn`, which you already have, and
`openssl` for a throwaway certificate.

In-memory repositories and a stub Trino: no PostgreSQL, no cluster, nothing
persisted. Every screen is populated with data chosen to show the states worth
looking at — a cluster that is degraded, a resource group that is throttling, a
benchmark comparison where one query fell off a cliff.

To host it somewhere, set `TMS_DEMO_PASSWORD` first; it refuses to start on the
default one, which is in this repository.

---

## A note on Trino versions

Everything version-specific in this project was measured against a real Trino
477 and recorded in [`docs/TRINO_VERIFIED.md`](../TRINO_VERIFIED.md) — including
several facts that turned out to be the opposite of the documented or assumed
behaviour.

If you run a different version, expect the JMX MBean names and some REST paths
to differ. `GET /v1/node`, for instance, does not exist in 477 at all. Start by
running `tms-config-check` and the health screen, and treat anything UNKNOWN as
a version difference until proven otherwise.
