# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI backend (already built, `src/tms/`). Frontend delegated: server-rendered
HTML + vanilla JS or a light framework, chosen at build time — must work behind
the existing FastAPI app (`src/tms/web/`), no separate Node deployment.

⛔ **Superseded on 2026-08-26 by DECISIONS.md D-016**: the frontend is being
rebuilt as a **React 19 SPA**, Vite-built to static assets that FastAPI serves.
What survives from the line above is the part that mattered — **no Node process
at runtime**; the deploy unit stays `pip install` + `systemctl restart`. What
is deliberately given up, and the mitigations that go with it, is written out
in D-016.

## Users

Platform team operators at a company running OSS Trino for ~50,000 internal
users. Primary scene: an operator at a desk (often during an incident or a
capacity review), multiple monitoring tools open, needs to answer "can the
clusters take queries right now, who is running what, and what did we do about
it" in seconds. Roles: viewer (read-only), operator (can kill queries), admin
(can tune health tests, export audit). UI language: **English** (confirmed
2026-08-06); backend advice strings are currently Korean and will need English
equivalents.

## Product Purpose

TMS (Trino Management Service) replaces the operational visibility that
Starburst Enterprise would provide, for a site that cannot use it. R1 answers:
what is running right now (live queries), is each cluster able to take queries
(synthetic health with remedies), who did what and why (append-only audit), and
where to look next (deep links into Grafana/Loki/the existing query-history
system). Success: an operator resolves "is it broken / who broke it / what do I
do" without SSH-ing into coordinators.

## Positioning

Not a metrics dashboard (Grafana owns charts), not a SQL editor (Superset), not
a log search (Loki). TMS's mechanism no neighbor copies: **synthetic
"can-it-take-queries" health where every non-GOOD state carries a written
remedy**, and **write actions that structurally cannot happen without a
recorded who/why** (the kill reason is even delivered to the query's owner).

## Operating Context

- 2 Trino 477 clusters (1 coordinator + 12 workers each), VM + systemd, no K8s.
- Data collected by a single `tms-collector` polling REST/JMX every 5–30s;
  the UI reads snapshots and must surface staleness honestly (stale > 30s).
- Sits alongside: Grafana (metrics), Superset (SQL), Trino Gateway UI, an
  existing query-history system (completed queries live there, not in TMS).
- Access control quirk that shapes UX: with `file` access control a permission
  denial on the query list arrives as an **empty list, not an error** — the UI
  must render the H-09 self-check distinctly from "no queries running".

## Capabilities and Constraints

R1 screens are bounded by the built API (`docs/API_R1.md`): portal/links,
live queries (list/detail/kill), cluster health (9 tests, roll-up, overrides),
audit (search/export). Non-goals are contractual (CLAUDE.md): no metrics
dashboard (Grafana), no SQL editor, no log viewer, no RBAC editor. The one
chart TMS draws is of its own benchmark results — data that exists nowhere
else; cluster resource utilization stays with Grafana. Write actions: kill
query, toggle health test/roll-up, change thresholds, export audit — all
require a reason; audit store down = writes disabled (503), which the UI must
present as intended behavior. States that must exist on every data surface:
fresh / stale / unknown-with-advice / empty / permission-degraded.

## Brand Commitments

- Name: **TMS** (Trino Management Service). No logo exists yet.
- Visual direction pinned by the user (2026-08-06): **modern monitoring SaaS**
  craft level — Datadog, Starburst Enterprise, Snowflake named as the bar;
  explicitly *not* Cloudera Manager's dated look.
- **Dark theme default, light toggle** (confirmed 2026-08-06).
- Subject affinity: Trino's brand accent is its magenta/pink family — available
  as a differentiator no generic monitoring tool would pick.

## Evidence on Hand

- Working backend with real data shapes: `docs/API_R1.md` response envelopes
  (`collected_at`/`stale`/`data`), health test catalog `docs/HEALTH_TESTS.md`.
- Live local verification stack: Trino 477 on :8443, PostgreSQL on :5433.
- Real measured numbers usable in mockups: ~3.5KB per query row, poll every 5s,
  `ActiveNodeCount` includes coordinator, kill message format
  ("Killed by TMS. actor=…, reason=…, request_id=…").
- No customer quotes, no benchmarks — do not invent any.

## Product Principles

1. Never render missing data as healthy — UNKNOWN outranks GOOD, stale wears a
   badge, permission problems name the fix (rules.json), not a generic error.
2. Every alarm ships its remedy — advice is first-class UI, not tooltip text.
3. Writes are ceremonies — target shown, reason required, consequence stated
   ("this reason is shown to the query's owner"), audit trail visible.
4. TMS is a hub, not a silo — deep links out (Grafana/Loki/history) are part of
   the product, rendered only when configured (no dead links).
5. Operator speed over decoration — dense tables, keyboard-friendly, scannable
   status at a glance.

## Accessibility & Inclusion

WCAG AA contrast in both themes; status never encoded by color alone (icons +
text labels accompany GOOD/CONCERNING/BAD/UNKNOWN).
