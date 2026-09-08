# PLAN — Test-suite audit and GitHub CI baseline

> **State**: current approved implementation slice
>
> **Baseline on 2026-09-08**: 909 tests and 79 subtests pass locally in 5.74 seconds
>
> **Scope**: test quality report plus deterministic GitHub CI; no product feature work

## Operator and maintainer outcome

Changes that break existing backend behavior, frontend type/build behavior, or the committed React
bundle fail automatically before merge. Maintainers also receive an evidence-backed test audit that
separates useful coverage from exact duplication and identifies the highest-risk missing cases.

CI automates repeatable checks. It does not replace real Trino, Gateway, PostgreSQL grant, SSH,
Ansible, systemd, or browser judgment in the onsite checklist.

## Non-goals

- no application feature or behavior change;
- no broad production-code refactor to make tests prettier;
- no Ruff cleanup or new lint baseline;
- no Python-version matrix, container build, release publishing, coverage percentage gate, or new
  test framework;
- no real infrastructure credentials or internal addresses in GitHub;
- no deletion based only on similar test names or code shape.

## Work 1 — Test audit

Inspect every test group and create docs/reviews/TEST_SUITE_AUDIT.md with:

1. the commands and environment used for the baseline;
2. a compact map from product invariants to the tests that enforce them;
3. exact or behaviorally redundant tests, with file/line evidence and which test should remain;
4. tests that pass without asserting the intended behavior or mock away the behavior under test;
5. missing P0/P1 regression cases, ordered by operational impact;
6. tests that require real infrastructure and therefore belong in TODO.md, not unit CI;
7. a recommendation for each item: keep, merge/delete, strengthen, add later, or onsite only.

Only remove or merge a test in this slice when equivalence is demonstrated and the remaining test
fails for the same regression. Do not pursue a target test count. Similar setup is not duplication
when it protects different behavior.

## Work 2 — Minimum CI

Add one GitHub Actions workflow for pushes and pull requests targeting main.

### Backend job

- use the supported Python floor, 3.9;
- install the existing dev extra;
- run python -m pytest -q;
- use no PostgreSQL, Trino, Gateway, SSH, or Ansible service because the default suite is designed
  to need no infrastructure.

### Frontend job

- use npm ci from the committed lock file;
- run the existing frontend lint/type check;
- run the production build;
- fail if the build leaves src/tms/ui/assets/ different from the committed bundle.

Use the current maintained stable major versions of official GitHub actions at implementation time.
Do not add third-party CI actions unless standard tools cannot perform the step.

## Acceptance conditions

- A fresh checkout can execute both CI jobs without internal secrets.
- The backend job reports the same passing suite as the local baseline or documents an explained,
  approved difference.
- A deliberately failing Python test makes the backend job fail.
- A TypeScript error or stale generated bundle makes the frontend job fail.
- The workflow does not run integration scripts that need internal infrastructure.
- docs/reviews/TEST_SUITE_AUDIT.md gives file/line evidence and no style-only opinions.
- Any test edits preserve or improve the invariant map and pass the complete suite.
- No application source under src/tms/ or product behavior changes in this slice.

## Likely files

- .github/workflows/ci.yml
- docs/reviews/TEST_SUITE_AUDIT.md
- tests files only when the audit proves a concrete merge, deletion, or missing P0/P1 check
- README.md only if the documented verification command changes

## Deterministic checks

    venv/bin/python -m pytest -q
    npm --prefix frontend run lint
    npm --prefix frontend run build
    git diff --exit-code -- src/tms/ui/assets

GitHub Actions must also pass on the feature branch or pull request. Local success alone does not
verify workflow syntax and runner behavior.

## Deferred

- browser automation;
- PostgreSQL-backed integration scripts;
- a Python-version matrix;
- coverage percentage gates;
- Ruff as a blocking gate;
- release publishing and dependency/security scanning.

Add one only when a separate plan names its operator or maintainer outcome.
