# Claude work order — Test-suite audit and GitHub CI baseline

Copy the following prompt into Claude Code.

---

You are the implementation owner for the current TMS slice.

Implement only **docs/plans/TEST_CI_BASELINE.md**.

Before implementation, read:

1. CLAUDE.md
2. docs/README.md
3. docs/ROADMAP.md section “Now — Test trust and GitHub CI”
4. docs/plans/TEST_CI_BASELINE.md
5. the tests and application call paths needed to prove each audit conclusion

The verified local baseline on 2026-09-08 is 909 tests and 79 subtests passing in 5.74 seconds.
Re-run it first. If the baseline does not pass, diagnose and report that difference before
changing CI.

Required deliverables:

- docs/reviews/TEST_SUITE_AUDIT.md with file/line evidence for exact duplication, ineffective tests,
  missing P0/P1 regression coverage, and tests that must remain onsite;
- one minimal GitHub Actions workflow with a Python 3.9 backend job and a frontend lint/build/bundle
  freshness job;
- test changes only when the audit proves a concrete duplicate or missing high-risk regression.

Do not implement application features or change product behavior. Do not refactor src/tms merely to
make tests shorter. Do not add a coverage gate, Ruff gate, Python matrix, container build, browser
job, PostgreSQL service, release pipeline, new test framework, or third-party action without a
demonstrated need.

Use branch **feat/test-ci-baseline** and start with:

    /develop test-suite audit and GitHub CI baseline from docs/plans/TEST_CI_BASELINE.md

Run every deterministic check in the plan. Build output under src/tms/ui/assets must remain
committed and clean. Create a local checkpoint commit, request independent review with:

    scripts/codex-review.sh --base main

Address every review finding with Accept, Reject, or Needs product-owner decision and evidence. Fix
accepted P0/P1 findings, rerun checks, request the final Codex pass, and push only the reviewed
feature branch. Never push feature work directly to main.

A NEEDS-HUMAN-DECISION tag blocks this work only when the current slice actually depends on it.
Historical and deferred tags do not stop unrelated work.
