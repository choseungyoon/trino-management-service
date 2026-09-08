# TMS project control center

This directory contains several generations of product, design, verification, and operating
documents. Do not read them all for every task. Start here and follow only the route that matches
the work.

## Current command chain

    Product Owner decides scope and risk
      -> Codex maintains roadmap, decisions, and one approved implementation plan
      -> Claude implements that one plan on a feature branch
      -> Codex performs an independent read-only review
      -> Claude fixes accepted findings
      -> Product Owner merges and schedules onsite verification

The current product sequence is in [ROADMAP.md](ROADMAP.md). Human and internal-environment work is
in [TODO.md](TODO.md). Claude's current work order is
[prompts/CLAUDE_NEXT.md](prompts/CLAUDE_NEXT.md).

## One owner per question

| Question | Canonical source | Do not use instead |
|---|---|---|
| What is TMS? | root [README.md](../README.md) | old Bolt descriptions |
| What happens next? | [ROADMAP.md](ROADMAP.md) | release tables in old design documents |
| What does the product promise? | [REQUIREMENTS.md](REQUIREMENTS.md) | backlog prose |
| Why was a durable choice made? | [DECISIONS.md](DECISIONS.md) | implementation comments |
| How is one approved slice built? | matching file under [plans/](plans/) | broad roadmap |
| What needs a person/internal network? | [TODO.md](TODO.md) | generated work-board snapshot |
| What is verified about Trino/Gateway/OPA? | [TRINO_VERIFIED.md](TRINO_VERIFIED.md) | memory or unverified docs |
| Who plans, builds, and reviews? | [TEAMS.md](TEAMS.md) | historical AI-DLC roles |
| How does an operator use it? | [usage/](usage/) | design documents |
| How is an internal operation performed? | [runbooks/](runbooks/) | usage overview |

When two documents conflict, use this order:

1. DECISIONS.md
2. TRINO_VERIFIED.md for external-system facts
3. the active plan
4. REQUIREMENTS.md
5. design/API reference documents
6. historical backlog, Bolt, and generated snapshots

Raise a conflict instead of silently choosing when the higher-priority source does not answer it.

## Reading routes

### Codex planning

Read root README.md, this file, ROADMAP.md, relevant decisions, relevant verified facts, and the
small set of design/runbook files touched by the proposed slice. Read TODO.md when the slice has
onsite dependencies.

### Claude implementation

Read root CLAUDE.md, this file, prompts/CLAUDE_NEXT.md, the one plan named there, only the decisions
referenced by that plan, and the touched code/tests. Do not ingest every historical document.

### Codex review

Read root AGENTS.md, the approved plan, referenced decisions/facts, changed tests, and the complete
diff. Review does not change application code.

### Operator

Start with [usage/README.md](usage/README.md). Use a runbook only for installation, migration, or an
internal operational procedure.

## Document classes

### Active control documents

- ROADMAP.md: ordered product strategy; Codex owns it.
- REQUIREMENTS.md: accepted behavior and boundaries; not a schedule.
- DECISIONS.md: durable decisions; Product Owner approval required.
- TODO.md: current human/onsite verification only.
- TEAMS.md: Codex–Claude delivery contract.
- plans/: approved or proposed implementation slices.
- prompts/CLAUDE_NEXT.md: replaceable current Claude work order.

### Operational documents

- usage/: operator-facing product documentation.
- runbooks/: step-by-step internal deployment and maintenance procedures.
- TRINO_VERIFIED.md: evidence ledger for Trino 477, Gateway, and OPA claims.

### Reference documents

ARCHITECTURE.md, API_R1.md, AUDIT_MODEL.md, HEALTH_TESTS.md, DESIGN_WL07.md, and
WORKLOAD_PROFILE.md explain existing contracts. They are consulted only when a change touches that
area.

### Historical or generated documents

- BACKLOG.md: original product-option assessment; not current priority.
- BOLTS.md, FRONTEND_PLAN.md, FRONTEND_PROGRESS.md, DESIGN_R2.md: completed development history.
- archive/: older evidence and plans kept for traceability.
- WORK_BOARD.md: generated internal-board snapshot; never edit by hand and never use over TODO.md or
  ROADMAP.md.
- MARKET_RESEARCH.md, PERF_MEASUREMENT.md, AIOPS.md: research or future strategy, not an approved
  implementation order.

Historical files remain at stable paths because decisions and generated snapshots cite them.
Moving them would create broken evidence links without reducing active reading.

## Update rules

- Codex updates ROADMAP.md when product priority changes.
- A durable scope, security, architecture, or operational-risk decision goes into DECISIONS.md
  after Product Owner approval; dependent prose is aligned in the same change.
- Claude updates application code, tests, relevant usage/config/API docs, and generated frontend
  assets for the approved slice. Claude does not rewrite decisions or roadmap priority.
- Completed plan files are retained and marked complete. prompts/CLAUDE_NEXT.md then points to the
  next plan.
- Detailed onsite status appears only in TODO.md; do not copy the checklist into roadmap or plans.

