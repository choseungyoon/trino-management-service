# TMS product roadmap

> **Owner**: Codex as service PM
>
> **Final authority**: Product Owner
>
> **Updated**: 2026-09-08

## Product strategy

TMS is the small operations console for teams running OSS Trino without Starburst Enterprise. It
coordinates the systems already responsible for traffic, metrics, logs, policy, and deployment; it
does not replace them or sit on the query path.

The delivery order is intentional:

1. trust the tests and automate repeatable checks;
2. finish validating what already exists;
3. make installation and contribution safe for an open-source audience;
4. add high-risk lifecycle automation only on that foundation;
5. integrate external observability and issue systems by context links and bounded APIs.

## Current status

| Area | State | Next gate |
|---|---|---|
| Database | migrations 001–028 applied onsite | remaining grant/append-only negative checks in TODO.md |
| Fleet | screen verified; idle worker shutdown verified | active-query, repeated-action, failure, and recovery cases |
| Workload | resource-group tree verified | write paths and audit/rollback verification |
| Benchmark | run, schedule, execution, and chart verified | read-only/abort/scheduler failure cases |
| Catalog | implemented, onsite incomplete | Ansible setup and deployment/rollback checks |
| Configuration | implemented, onsite incomplete | scan, deploy, drift, and rollback checks |
| Safe Restart | implemented, onsite incomplete | configured Ansible and full sequence/failure checks |
| Automated CI | absent | current work order |
| In-place upgrade | decision and plan complete | Slice 0 environment verification |

The detailed internal checklist lives only in TODO.md.

## Ordered delivery

### Now — Test trust and GitHub CI

Plan: [plans/TEST_CI_BASELINE.md](plans/TEST_CI_BASELINE.md)

- Audit the current Claude-generated tests for exact duplication, false confidence, and missing
  P0/P1 regression coverage.
- Put deterministic backend and frontend checks in GitHub Actions.
- Keep real Trino, Gateway, PostgreSQL grants, SSH, Ansible, and systemd checks onsite.

Exit: a pull request cannot merge with a broken unit/API suite or stale committed frontend bundle,
and the audit leaves a prioritized, evidence-backed follow-up list.

### Next — Finish current onsite verification

Source: [TODO.md](TODO.md)

- Complete Ansible configuration once.
- Validate Catalog, Configuration, and Safe Restart end to end.
- Exercise unsafe and failure cases for Resource Group, Fleet shutdown, and Benchmark.
- Fix only reproducible failures; do not mix new features into verification fixes.

Exit: every already-exposed operational write has a successful path, a failure path, audit proof,
and a documented recovery path on the real environment.

### Then — Open-source readiness

- Choose and add a license. **[NEEDS-HUMAN-DECISION]**
- Reduce first-install steps and provide a safe public example configuration without credentials or
  internal topology.
- Make CI and contribution instructions the default verification path.
- Validate a clean install from the public repository on a fresh supported Python environment.
- Document the boundary between automated CI and onsite infrastructure verification.

Exit: an external Trino operator can understand the product, install a non-production instance,
run checks, and contribute without internal knowledge.

### Lifecycle 1 — VM in-place Trino upgrade

Decision: D-020

Plan: [plans/INPLACE_UPGRADE.md](plans/INPLACE_UPGRADE.md)

Order:

1. verify archive, Java, systemd, install, and rollback facts on a development cluster;
2. build the immutable artifact registry;
3. build read-only preflight and current-release bootstrap;
4. expose one complete upgrade with immediate rollback;
5. add known-good release history rollback.

No destructive start button appears before the complete safe sequence and rollback path exist.

### Lifecycle 2 — Provisioning and expansion

- Add a worker to an existing cluster from pre-authorized VM information.
- Build a new coordinator/worker cluster using configured Ansible and Java/Trino/config/catalog
  contracts.
- Register a completed cluster with Gateway only after health and benchmark verification.

This work starts only after upgrade primitives prove that artifact, inventory, execution history,
and rollback are reliable. It reuses those primitives instead of creating another deployment
framework.

### Integrations — Logs, incidents, and observability context

- Deep-link from cluster, node, query, benchmark, and operation context into the configured external
  log/metric system.
- Add bounded issue-system integration only after the target system and ownership are approved.
- Keep alert evaluation, metric storage, log search, and incident workflow in their existing
  systems.

### Later — AIOps

AIOPS.md remains research. Automation starts with recommendations and recorded evidence. No
autonomous cluster write is planned until the underlying manual operation has enough successful and
failed history to define a safe policy.

## Product boundaries

Do not add a SQL editor, general metrics dashboard, alert engine, log store/search, RBAC editor,
data catalog, LLM assistant, static weighted routing, arbitrary SSH command runner, or general
workflow platform. Reconsider a boundary only through an explicit Product Owner decision.

