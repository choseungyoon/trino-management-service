# Codex planning and review instructions

Before planning or reviewing work, follow the reading route in `docs/README.md`.

Always read `README.md`, `docs/ROADMAP.md`, the active plan, and the decisions referenced by that
plan. Read only the relevant design/API/runbook files. Read `docs/TODO.md` when onsite work is in
scope, and `docs/TRINO_VERIFIED.md` for every Trino, Gateway, or OPA claim. Historical Bolt,
backlog, and generated-board files are not current instructions.

The product owner has final authority. Codex owns planning, source-of-truth document updates after
an explicit decision, and independent code review. Claude owns implementation and review fixes.
During a review, Codex does not edit application code.

## Project invariants

- TMS is never on the query path. If TMS stops, queries must continue.
- Only facts verified for Trino 477 in `docs/TRINO_VERIFIED.md` may enter code or operational advice.
- Every Trino/cluster operational write requires an administrator, a non-blank `reason`, confirmation
  in the UI, and an audit record. The work board follows D-013; aborting a benchmark is the
  documented safe-stop exception. If a required action cannot be tracked safely, refuse it.
- Restart order is fixed: stop intake, confirm it stopped, drain, restart, verify health, restore
  traffic. Worker removal requires graceful shutdown first.
- Do not add a SQL editor, metrics dashboard, alert engine, log store/search, RBAC editor, data
  catalog, LLM assistant, or static weighted routing. Use the named external systems instead.
- Python remains 3.9 compatible. Code comments are short English explanations, not internal
  document-history notes.
- The repository is public. Never commit credentials, internal hosts, IPs, or private URLs.
- The React bundle under `src/tms/ui/assets/` is committed. Any frontend change must run
  `npm --prefix frontend run build` and include the output.
- A `[NEEDS-HUMAN-DECISION]` tag blocks work only when the current plan or implementation actually
  depends on that decision. Historical or deferred tags do not stop unrelated work.

The intended audience is two or three internal Trino operators. Planned TMS maintenance may make
the console temporarily unavailable; it must not affect query traffic.

## Planning contract

Plan one smallest complete slice at a time. State the operator outcome, affected invariants,
acceptance conditions, files likely to change, deterministic checks, and any required onsite
verification. A durable product or architecture decision needs product-owner approval and a
`docs/DECISIONS.md` update before implementation. Codex keeps `docs/ROADMAP.md` and the active
plan aligned; Claude does not choose roadmap priority.

## Review priority

1. **P0:** query-path impact, authorization bypass, secret exposure, data loss, destructive-action
   sequence bypass, or a write that can occur without its required audit/reason gate.
2. **P1:** reproducible correctness bug, verified-Trino contradiction, unsafe state transition,
   stale/unknown data shown as healthy, race, or missing regression coverage for changed logic.
3. **P2:** concrete maintainability, accessibility, performance, or operator-UX impact.

Ignore style-only preferences, speculative abstractions, and unrelated pre-existing issues unless
they are required to prove a regression.

## Finding format

Return findings first, ordered by severity:

`[P0|P1|P2] path:line — short title`

For each finding, include evidence, operator/system impact, the violated invariant or decision, and
one testable acceptance condition. If there are no actionable findings, say so plainly. End with
residual testing risk.
