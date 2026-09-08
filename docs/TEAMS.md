# Codex–Claude collaboration workflow

## Roles and authority

- **Product owner:** final authority for scope, architecture, operational risk, and release decisions.
- **Codex:** plans one approved slice, updates source-of-truth documents after decisions, and performs
  independent read-only review.
- **Claude:** implements approved slices, writes tests, runs checks, fixes review findings, and
  delivers feature branches.

The retired AI-DLC role simulation is historical context in `BOLTS.md`; it is not the current
development process. `README.md` is the document map and `ROADMAP.md` is the only product-priority
list.

## Sources of truth

| Question | Source |
|---|---|
| What the product is and deliberately is not | `README.md` |
| Which work happens next | `ROADMAP.md` |
| Accepted durable decisions | `DECISIONS.md` |
| How the current slice is implemented | active file under `plans/` |
| Verified Trino 477, Gateway, and OPA facts | `TRINO_VERIFIED.md` |
| Human and onsite work still open | `TODO.md` |
| Current board snapshot from the internal database | `WORK_BOARD.md` |
| Feature behavior and API details | relevant design, API, usage, and runbook file |
| Completed historical construction | `BOLTS.md` and `archive/` |

`ROADMAP.md` owns product order. `TODO.md` owns onsite detail and wins over an older
`WORK_BOARD.md`. Never edit `WORK_BOARD.md` manually; regenerate it with `tms-work-export`
inside the network. If a durable decision conflicts with dependent prose, update `DECISIONS.md`
first and then align the dependent document.

## Default development loop

```text
request
  -> Codex proposes one reviewable slice
  -> product owner approves
  -> Claude /develop implements on feat/<task>
  -> deterministic checks pass
  -> local checkpoint commit
  -> Codex reviews against main
  -> Claude fixes accepted P0/P1 findings
  -> checks and one final Codex review
  -> reviewed feature branch is pushed
  -> product owner merges and schedules onsite verification
```

One slice stays active at a time. Git provides the exact review boundary; no polling or separate
message queue is required between Claude and Codex.

## Planning contract

Codex states:

1. operator outcome and why the work is needed;
2. explicit non-goals;
3. affected invariants and verified Trino facts;
4. observable acceptance conditions;
5. likely files and migrations;
6. deterministic checks and required onsite verification;
7. rollback or disable path for risky operational changes.

Plans stop for product-owner approval only when they change durable scope, architecture, security,
or operational risk. A `[NEEDS-HUMAN-DECISION]` tag outside the active slice does not block it.

## Implementation contract

Claude starts with `/develop <approved task>` and follows `.claude/skills/develop/SKILL.md`. It must
preserve unrelated work, use the smallest complete change, add focused regression coverage, and
keep unfinished work off `main`.

If a feature changes a screen or configuration, update the matching English usage document. If it
changes a durable decision, stop for the product owner; Claude does not rewrite decisions to match
its implementation.

## Review contract

Codex reads `AGENTS.md`, the relevant decisions and verified facts, tests, and the complete diff.
Codex does not edit application code during review. Findings use the P0/P1/P2 format in `AGENTS.md`
and must describe a concrete failure mode and testable correction.

Claude answers each finding with **Accept**, **Reject**, or **Needs product-owner decision**, with
evidence. P0/P1 must be fixed or explicitly resolved. P2 may be deferred with a reason. Stop after
two Codex passes; unresolved P0/P1 becomes `PO_DECISION_NEEDED`.

If Codex is unavailable, keep the checkpoint local and report `IMPLEMENTED_UNREVIEWED`. Do not call
the slice ready or push it through the automated flow.

## Verification gates

- Default Python suite: `venv/bin/python -m pytest -q`
- Frontend type check: `npm --prefix frontend run lint`
- Frontend build: `npm --prefix frontend run build`, with built assets committed
- Integration/browser checks when the changed behavior crosses those boundaries
- Onsite validation for real Trino, Gateway, PostgreSQL grants, SSH, Ansible, or systemd behavior

Ruff is currently informative, not blocking: the repository has a large pre-existing baseline.
Cleaning it is a separate slice, not something mixed into feature work.

## Git safety

- Feature branches use `feat/<short-task>`.
- Never push `main`, force-push, rebase shared work, or discard unrelated changes.
- `scripts/codex-review.sh --base main` invokes the independent review.
- `scripts/push-current-feature.sh` refuses protected branches.
