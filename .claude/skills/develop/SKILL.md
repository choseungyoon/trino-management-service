---
name: develop
description: Implement one approved TMS slice, verify it, and run an independent Codex review.
argument-hint: "<approved task>"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(git status *), Bash(git diff *), Bash(git branch *), Bash(git switch *), Bash(git add *), Bash(git commit *), Bash(venv/bin/python *), Bash(venv/bin/ruff *), Bash(npm --prefix frontend run *), Bash(./scripts/codex-review.sh *), Bash(./scripts/push-current-feature.sh)
---

Task: $ARGUMENTS

1. Confirm the product owner or Codex approved this exact slice. Otherwise stop with
   `PO_DECISION_NEEDED`.
2. Read `CLAUDE.md`, `docs/TEAMS.md`, `docs/TODO.md`, and only the relevant design,
   decision, Trino verification, API, and usage documents.
3. Preserve unrelated work. Create a short `feat/<task>` branch; never develop or push on
   `main`.
4. State the operator scenario, invariants, acceptance conditions, and exact checks. Implement
   the smallest complete vertical slice.
5. Add the smallest regression test for changed behavior. For frontend changes, run
   `npm --prefix frontend run build` and include `src/tms/ui/assets/`.
6. Run the relevant checks. The minimum is `venv/bin/python -m pytest -q`; also run
   `npm --prefix frontend run lint` for frontend changes. Use targeted integration or browser
   checks when the behavior needs them. Ruff is not a gate until its existing baseline is clean.
7. Commit only this slice as a local review checkpoint, then run
   `./scripts/codex-review.sh --base main`.
8. Answer every finding with **Accept**, **Reject**, or **Needs product-owner decision**, plus
   evidence. Fix accepted P0/P1 findings, rerun checks, checkpoint, and request one final Codex
   review. After two passes, unresolved P0/P1 becomes `PO_DECISION_NEEDED`.
9. If Codex is unavailable, keep the checkpoint local and report `IMPLEMENTED_UNREVIEWED`.
   Otherwise push only the feature branch with `./scripts/push-current-feature.sh` and report
   its name, checks, review dispositions, commit SHA, and deferred P2 findings.
