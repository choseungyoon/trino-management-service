# Claude Code implementation instructions

Before implementation, follow `docs/README.md` and the current work order in
`docs/prompts/CLAUDE_NEXT.md`. Read the one plan named there, only its referenced decisions and
relevant design/API/runbook files, and `docs/TRINO_VERIFIED.md` for every Trino, Gateway, or OPA
claim. Historical Bolt, backlog, and generated-board files are not implementation instructions.

The product owner has final authority. Codex owns planning and independent review. Claude owns
application implementation, tests, deterministic verification, review fixes, and feature-branch
delivery.

Start approved work with `/develop <approved task>`. Implement one smallest complete vertical slice,
checkpoint it locally, invoke `scripts/codex-review.sh --base main`, address every finding, and push
only the reviewed feature branch. Never push feature work directly to `main`.

## Non-negotiable implementation rules

- TMS is never on the query path. If TMS stops, queries must continue.
- Only facts verified for Trino 477 in `docs/TRINO_VERIFIED.md` may enter code or operational advice.
- Every Trino/cluster operational write requires an administrator, a non-blank `reason`, confirmation
  in the UI, and an audit record. The work board follows D-013; aborting a benchmark is the
  documented safe-stop exception. If a required action cannot be tracked safely, refuse it.
- Restart order is fixed: stop intake, confirm it stopped, drain, restart, verify health, restore
  traffic. Worker removal requires graceful shutdown first.
- Do not add a SQL editor, metrics dashboard, alert engine, log store/search, RBAC editor, data
  catalog, LLM assistant, or static weighted routing.
- External dependencies are optional and bounded by timeouts. Missing or stale data is never healthy.
- Python remains 3.9 compatible. Keep comments short and in English.
- The repository is public. Never commit credentials, internal hosts, IPs, or private URLs.
- The React console uses the server session cookie; never move authentication to `localStorage`.
- Frontend changes must run `npm --prefix frontend run build` and commit
  `src/tms/ui/assets/`, because deployment hosts have no Node runtime.
- A `[NEEDS-HUMAN-DECISION]` tag blocks work only when the current slice depends on it.

The intended audience is two or three internal Trino operators. Planned TMS maintenance may make
the console temporarily unavailable; it must not affect query traffic.

## Source boundaries

- API transport: `src/tms/api/routes/`
- Business rules: services and domain modules under `src/tms/`
- Trino/Gateway clients: `src/tms/clients/`
- React screens: `frontend/src/screens/`; shared UI: `frontend/src/components/`
- Approved CSS vocabulary: `frontend/src/tms.css`; do not invent unused class names
- Built console: `src/tms/ui/assets/`
- Current product order: `docs/ROADMAP.md`
- Current approved plan: the file named by `docs/prompts/CLAUDE_NEXT.md`
- Current human/onsite work: `docs/TODO.md`
- Durable decisions: `docs/DECISIONS.md`
- Verified version-specific facts: `docs/TRINO_VERIFIED.md`

Prefer existing helpers, standard library, native browser behavior, and installed dependencies.
Do not add abstractions or configuration for hypothetical future use.

## Minimum verification

```bash
venv/bin/python -m pytest -q
npm --prefix frontend run lint        # when frontend code changes
npm --prefix frontend run build       # when frontend code changes; commit output
```

Use the relevant integration, browser, or onsite check when the behavior cannot be proven by the
default suite. Ruff is not a merge gate until its existing baseline is cleaned separately.
