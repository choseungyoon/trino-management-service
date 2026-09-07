# TODO — current human and onsite work

> Updated 2026-09-05. This file contains only work that needs a person, the internal network, or a
> real cluster. Product decisions belong in `DECISIONS.md`; completed construction belongs in
> `BOLTS.md`. `WORK_BOARD.md` is generated from the internal board and may be older than this file.

## Current verification status

| Area | Verified onsite | Still unverified |
|---|---|---|
| Database | Migrations `001` through `028` applied | Grant/append-only negative checks listed below |
| Console | Fleet, Workload, and Benchmark render with internal data | Catalog, Configuration, Safe Restart completion |
| Fleet | Fleet data renders; an idle worker accepted shutdown and stopped | Graceful drain with running tasks; query failures remain zero |
| Workload | Resource-group activity tree renders | Distinguishing missing `jmxExport` from no activity |
| Resource Groups | Configured groups render | Edit, validation refusal, history, revert, audit, DB grants |
| Benchmark | Runs, schedules, scheduled execution, and charts render | Negative/concurrency and append-only checks below |
| Catalog | Not verified | Ansible setup, development deployment, restart, promotion gate |
| Configuration | Not verified | Ansible scan, drift, merged deployment, restart, promotion gate |
| Safe Restart | Not completed | Ansible setup and the complete traffic/drain/health sequence |

TMS is in internal verification before launch. It is used by two or three Trino operators. Planned
console downtime is acceptable; interrupting query traffic is not.

## 1. Configure Ansible once

Catalog, Configuration, and automated Safe Restart are blocked by the same missing setup. Complete
this before testing any of those screens.

- [ ] Install the approved restart playbook and `docs/templates/collect-config.yml`,
      `deploy-catalog.yml`, and `deploy-config.yml` under `/etc/tms/ansible/`.
- [ ] Set each template's `trino_etc`, `trino_log`, `trino_user`, and `trino_group` for the real
      hosts.
- [ ] Put the absolute playbook paths, inventory paths, state directory, and SSH material in the
      secret/internal configuration. Do not commit internal paths or hosts.
- [ ] Run each playbook manually against the development cluster first.
- [ ] Run `tms-config-check`, then restart `tms-api` and `tms-collector`.

The security decision to give the TMS host SSH access is already recorded in D-009. Do not add a
second execution path or user-selectable playbook.

## 2. Configuration scan and drift

- [ ] Open `/cluster-config`, run **Read the nodes**, and confirm every expected node appears.
- [ ] Confirm coordinator/worker role differences are not reported as drift.
- [ ] Change one development worker and confirm peer drift is reported.
- [ ] Confirm `node.properties` appears as expected differences.
- [ ] Confirm catalogs expose checksums only, never file contents or secrets.
- [ ] Confirm known-property counts are populated from Trino logs.
- [ ] Confirm viewers can read results but cannot start a scan.
- [ ] Stop one development worker and confirm its absence is not reported as configuration drift.

## 3. Catalog deployment

Use a harmless catalog on the development cluster first.

- [ ] Create a `memory` catalog draft and confirm literal secret values are rejected in favor of
      `${ENV:VAR}` references.
- [ ] Confirm production deployment stays disabled until the exact draft succeeds in development.
- [ ] Deploy to development and verify the file exists on every node.
- [ ] Complete Safe Restart and verify the catalog appears after restart.
- [ ] Confirm editing the draft clears its development proof.
- [ ] Confirm `CATALOG_CHANGE` and `CATALOG_DEPLOY` audit records include the reason.
- [ ] Test a broken connector only on development and prove the documented recovery path.

## 4. Configuration editing and deployment

- [ ] Create a development-only `config.properties` change and confirm unknown property names are
      refused.
- [ ] Confirm literal secrets are refused in favor of `${ENV:VAR}`.
- [ ] Deploy to development and verify the intended lines changed while every unrelated line,
      especially keystore settings, stayed intact.
- [ ] Restart safely and confirm the setting took effect.
- [ ] Confirm production deployment stays disabled until the exact change succeeds in development.
- [ ] Confirm editing the change clears its development proof.
- [ ] Confirm `CONFIG_CHANGE` and `CONFIG_DEPLOY` audit records include the reason.
- [ ] Verify that a valid property name with an invalid value fails at Trino startup only on the
      development cluster, then recover using the documented rollback.

## 5. Safe Restart

- [ ] Start on the development cluster and confirm the Gateway backend is deactivated before drain.
- [ ] Keep a query running and confirm restart is blocked while the query remains.
- [ ] Let it finish and confirm the configured Ansible restart runs only after the cluster is empty.
- [ ] Confirm traffic is not restored until health is `GOOD`.
- [ ] Exercise abort before and after drain; both must restore Gateway traffic.
- [ ] Force-drain only with a test query and a second explicit reason; confirm the warning and audit.
- [ ] Simulate playbook failure and confirm the cluster remains visibly out of rotation until an
      operator chooses recovery.

## 6. Resource Group writes

Use an unused group first; changes reach Trino within the DB refresh interval.

- [ ] Edit one value and confirm the reason is required and the change appears within 10 seconds.
- [ ] Submit an invalid value and confirm it is refused without losing the entered form values.
- [ ] Create/delete a group and selector; confirm missing group-provider support produces a warning.
- [ ] Confirm history, revert, and the revert's own new revision.
- [ ] Confirm `RESOURCE_GROUP_CHANGE` and `RESOURCE_GROUP_REVERT` audit records.
- [ ] As `tms_app`, confirm update/delete of revision history is denied.

## 7. Fleet and graceful shutdown

The completed idle-node test proves only the shutdown request and process stop.

- [ ] Run a controlled query with tasks on one development worker.
- [ ] Start shutdown from Fleet and observe drain through process exit.
- [ ] Confirm zero query failures and allow at least twice Trino's configured shutdown grace period.
- [ ] Confirm `NODE_SHUTDOWN` audit details identify the node, actor, and reason.
- [ ] If `fleet.source: tms` is enabled, separately verify scan, generated inventory, retention of an
      unreachable node, manual add/remove, and the `CLUSTER_NODE_CHANGE` audit trail.

## 8. Benchmark safety checks

The positive execution, schedule, and chart paths are verified.

- [ ] Confirm a second concurrent run on the same cluster is refused.
- [ ] Compare equivalent runs across clusters and confirm differing traffic conditions are warned.
- [ ] Edit a query and confirm old runs retain their original SQL and comparisons mark the change.
- [ ] Confirm query-set edits are refused while the set is running.
- [ ] Confirm write SQL remains refused even when hidden after comments.
- [ ] Confirm schedule edits/deletes and runs carry the creating actor and reason in audit records.
- [ ] As `tms_app`, confirm benchmark result update/delete is denied.

## 9. Remaining operational work

- [ ] Recheck append-only grants for audit and restart event tables as `tms_app`.
- [ ] Measure NFR-PERF-03 during peak traffic and update `PERF_MEASUREMENT.md`; set the workload poll
      interval from evidence.
- [ ] Move the Gateway database off VM1 and provide HA before depending on it for production routing.
- [ ] Deploy worker OPA policy required for graceful shutdown and monitor worker OPA availability.
- [ ] Configure Grafana/Prometheus and Loki/OpenSearch, then fill the external deep-link templates.
- [ ] Add named operator accounts; do not share an admin identity because it destroys audit value.
- [ ] Replace LB IP hash with the chosen session-affinity policy after Gateway DB HA.
- [ ] Regenerate `WORK_BOARD.md` with `tms-work-export` inside the network and align board statuses.

## Deferred decisions

These do not block the current verification work:

- SLO targets wait for the production workload measurement.
- Dynamic catalog storage (`catalog.store=file` versus `memory`) remains an R4 decision; current
  file deployment and restart testing does not require it.
- Completed-query history stays in the existing external project unless integration requirements
  reopen that decision.
