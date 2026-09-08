# Test suite audit

> **Scope**: evidence for the slice in [plans/TEST_CI_BASELINE.md](../plans/TEST_CI_BASELINE.md).
> No application behaviour was changed. Test edits in this slice are limited to the three items
> marked **done** below, each demonstrated by mutation.

## 1. Baseline and environment

| | |
|---|---|
| Date | 2026-09-08 |
| Command | `venv/bin/python -m pytest -q` |
| Before this slice | **909 passed, 79 subtests** (Python 3.13.3, pytest 9.1.1) |
| After this slice | **912 passed, 79 subtests** — three added, none removed |
| CI floor verified | **912 passed** (Python 3.9.22, pytest 8.4.2) |
| Frontend | `npm --prefix frontend ci && run lint && run build`, then a clean-tree check on `src/tms/ui/assets` |

### ⛔ The 3.9 run reports no subtest line, and that is not a coverage loss

pytest 8 folds `unittest.subTest` results into the parent test; pytest 9 reports them separately.
Both **fail the run** when a subtest fails, which was measured rather than assumed:

```
Python 3.9.22  / pytest 8.4.2 → 1 failed
Python 3.13.3  / pytest 9.1.1 → SUBFAILED(value=2) … 1 failed, 2 subtests passed
```

So CI on the 3.9 floor will print `912 passed` with no subtest count. That is the documented,
approved difference from the local baseline required by the plan's acceptance conditions.

### Instrumentation note

`coverage` was installed **into a throwaway virtualenv only** to produce the numbers below. It is
not a project dependency and is deliberately not a CI gate.

## 2. Invariant map

Where the product's non-negotiable rules are actually enforced by a test.

| Invariant | Enforced by | Coverage |
|---|---|---|
| TMS never proxies queries; the collector runs no SQL | `tests/test_sql_isolation.py` | `tms/collector` 46–100% |
| Every write carries a reason and an audit row | `tests/test_audit.py`, per-service `PermissionTest` classes | `tms/core/audit.py` 95% |
| Audit is append-only | `tests/test_audit.py`, `migrations/*_grants.sql` | grants are **onsite only** (§6) |
| Restart never skips drain | `tests/test_safe_sequence.py:1`, `tests/test_restart_service.py` | `tms/ops/sequence.py` 99% |
| A restart is refused on an empty inventory | `tests/test_ansible_executor.py` `EmptyInventoryTest` | `tms/ops/ansible.py` 94% |
| No host name reaches the Ansible command line | `tests/test_ansible_executor.py`, `tests/test_config_edit.py:117` | 94% |
| Playbook output is redacted | `tests/test_ansible_executor.py:348` `RedactionTest` | `tms/ops/process.py` 89% |
| Credentials must be `${ENV:VAR}` | `tests/test_catalogs.py`, `tests/test_config_edit.py:137` | `tms/ops/configedit.py`, `catalogs.py` |
| Development cluster gates production deploys | `tests/test_catalog_service.py`, `tests/test_config_edit.py:222` | `configeditservice.py` 74% |
| An unknown Trino property is refused | `tests/test_config_edit.py:170` `TypoGateTest` | — |
| Discovery never removes a node | `tests/test_cluster_nodes.py` `PlanRefreshTest` | `tms/fleet/nodes.py` |
| Console styles exist in the design system | `tests/test_console_styles.py` | — |

Measured total: **77% of `src/tms`** (8588 statements, 1933 uncovered).

## 3. Exact or behaviourally redundant tests

**Result: none found. No test is deleted or merged in this slice.**

An AST comparison of all 909 test bodies with constants preserved produced **zero** identical
bodies. A second pass that normalised string constants produced 12 structurally identical groups;
every one of those is the same assertion shape applied to *different inputs*, which the plan
explicitly excludes from duplication.

### Worked example — the strongest candidate is not redundant

`tests/test_ansible_executor.py:367` and `:373` are the closest pair in the suite: both are a
single `assertEqual(text, redact(text))`.

Mutating `src/tms/ops/process.py:34` so `pass` matches as a prefix
(`[\w-]*pass` → `[\w-]*pass[\w-]*`) gives:

```
FAILED test_a_recap_that_merely_contains_pass_is_not_redacted   (line 373)
PASSED test_ordinary_output_survives                            (line 367)
```

Line 373 protects against over-redaction of `passed=12` in a PLAY RECAP; line 367 protects the
generic recap that contains no secret-shaped token at all. **Deleting either loses a regression.**

> A first mutation attempt (removing the trailing `\b`) changed nothing, because the following
> group already requires `:` or `=`. Recorded because it means that `\b` is inert for these inputs
> — a production observation, out of scope here, listed in §5 as a later item.

## 4. Tests that pass without proving the intended behaviour

Four tests contain no assertion (AST scan for `assert*`/`raises`/`fail`):

| Location | Verdict |
|---|---|
| `tests/test_catalog_service.py:161` `test_removing_needs_no_proof` | **Was blind — fixed** |
| `tests/test_config_edit.py:183` `test_removing_an_unknown_name_is_allowed` | **Was blind — fixed** |
| `tests/test_benchmark.py:72` `test_read_only_starts_are_accepted` | Keep. "Does not raise" *is* the behaviour, and `build_query_sets` returning nothing would fail the sibling assertions at `:64` |
| `tests/test_audit.py:176` `test_storage_failure_after_the_action_does_not_mask_success` | Keep. The contract is "the `with` block completes"; there is nothing else to assert |

### ⛔ The two removal tests passed while the deployment failed

Both call a helper that returns when the run **ends**, not when it **succeeds**:

```python
# tests/test_catalog_service.py — wait() only polls is_busy()
service.deploy(ADMIN, draft["id"], "prod-a", reason="clean up", action="remove")
wait(service, "prod-a")
```

Driving the same fixture with a playbook exiting 2 gave `deployment state: FAILED` and the test
still passed. **Done**: one assertion added to each. Re-running the catalog test under a failing
playbook now yields `AssertionError: 'SUCCEEDED' != 'FAILED'`.

### ⛔ 49 tests were invisible to direct invocation — fixed

Found while acting on a review finding about the file this slice touched, then checked across the
whole suite rather than only where it was reported.

Every test file ends with `if __name__ == "__main__": unittest.main()`. In 14 files that guard sat
*before* one or more `class …Test` definitions, so `unittest.main()` ran at that line and never saw
the classes below it:

```
venv/bin/python tests/test_bench_trend.py   → Ran 10 tests … OK
venv/bin/python -m pytest tests/test_bench_trend.py  → 18 collected
```

| File | Ran directly | Collected | Hidden |
|---|---|---|---|
| `test_bench_trend.py` | 10 | 18 | 8 |
| `test_api_services.py` | 39 | 45 | 6 |
| `test_api_work_fleet.py` | 10 | 15 | 5 |
| `test_collector_poller.py`, `test_collector_service.py`, `test_config.py`, `test_health_observed.py` | | | 4 each |
| `test_configscan.py`, `test_console_styles.py` | | | 3 each |
| `test_ansible_executor.py`, `test_collector_units.py`, `test_configcheck.py` | | | 2 each |
| `test_fleet.py`, `test_restart_service.py` | | | 1 each |
| **total** | **249** | **298** | **49** |

CI was never affected — pytest imports the module, so `__main__` never runs and all 912 are
collected. The damage is developer-facing and is exactly the false confidence this audit is for: a
green `OK` while a third of a file never executed.

**Done**: the guard moved to the end of file in all 14 files. Pure statement reordering; no test
logic changed. Every file now reports the same count both ways, verified programmatically.

## 5. Tests that mock away the behaviour under test

**Result: none found, and the suite's design is the reason.**

No test file imports `unittest.mock`, `MagicMock`, or `monkeypatch`. (The `patch(` occurrences in
`tests/test_api_resource_groups.py:115` are HTTP `PATCH` requests.) Collaborators are hand-written
fakes injected through constructors — `InMemory*Repository`, `FakeTrinoClient`, and the `runner=`
callable on each Ansible-backed service.

The `runner=lambda command, timeout, on_line: {"rc": 0}` stub used by
`tests/test_catalog_service.py:41` and `tests/test_config_edit.py:73` replaces the *subprocess*,
not the behaviour under test: those tests assert the gate decisions and the constructed command.
Whether `ansible-playbook` then does the right thing is §6 work, not unit work.

## 6. Tests that need real infrastructure — onsite only, not CI

Correctly excluded already: these are named `smoke_*.py` / plain module names, so `testpaths =
["tests"]` never collects them. Confirmed: 0 of the 909 collected tests come from these directories.

| Path | Needs | Belongs in |
|---|---|---|
| `tests/integration/smoke_api_postgres.py` | real PostgreSQL | `docs/TODO.md` |
| `tests/integration/smoke_benchmark.py` | PostgreSQL append-only **grants** | `docs/TODO.md` V-8 |
| `tests/integration/smoke_resource_groups.py` | PostgreSQL, Trino `db` manager | `docs/TODO.md` V-9 |
| `tests/integration/smoke_work_board.py` | PostgreSQL | `docs/TODO.md` |
| `tests/browser/ui_behaviour.py`, `screenshots.py` | Chromium via the `[browser]` extra | deferred by the plan |

This is why `tms/core/audit_postgres.py` (22%), `tms/collector/postgres.py` (27%) and the `*store.py`
repositories sit low in the coverage table. Those numbers are **not** a CI gap: the behaviour that
matters there is the database grant, which only a real database can refuse.

### One defect found here

`tests/integration/smoke_api_postgres.py:3` hardcodes an absolute developer path:

```python
sys.path.insert(0, "/Users/seungyuncho/Product/trino-management-service/src")
```

It fails for any other checkout, and this repository is public. **Recommendation: fix during the
open-source readiness milestone** — out of this slice's scope, which forbids unrelated edits.

## 7. Missing P0/P1 regression coverage, by operational impact

Two production modules had **0% coverage**, and both are shipped console scripts declared in
`pyproject.toml:45,48`.

### P0 — `tms/fleet/importer.py` (`tms-import-inventory`) — **done**

63 statements, 0% covered before this slice. It is the one-time carry of a hand-maintained Ansible
inventory into the node list (D-019), and the node list **renders the inventory that restart and
configuration deployment target**. A host it drops silently stops receiving configuration; a host it
adds twice becomes a duplicated deploy target. The operator sees only a count.

`plan()` needs nothing but a temporary file, so this is unit-testable. Three tests added to
`tests/test_cluster_nodes.py` `ImportPlanTest`, each verified by mutating `importer.py`:

| Mutation | Result |
|---|---|
| `if n.host not in known` → `if True` (duplicate hosts) | 1 failed |
| `if not os.path.isfile(path)` → `if False` (silent wrong path) | 1 failed |

The third case is the dangerous one: a wrong path previously returned "nothing to add", which reads
exactly like "already imported".

### P1 — `tms/work/export.py` (`tms-work-export`), 51 statements, 0%

Writes `docs/WORK_BOARD.md`, which is the only way to read the board from outside the network.
Impact is a stale document, not a cluster. **Recommendation: add later**, not in this slice.

### P1 — `tms/bench/ticker.py`, 57% (missing 44–48, 51, 57–64)

The scheduler thread body. Untested paths are the failure/backoff branches. **Add later.**

### P2 — `tms/api/deeplinks.py`, 79% (missing 20, 59–62, 75–76, 84–85)

The malformed-template fallbacks. A recent production defect in this area
(`links()` rendering Grafana with an empty cluster) was fixed with tests on 2026-09-07.
**Recommendation: strengthen when next touched.**

### Not a gap

`tms.api.routes.*` appeared unimported by any test, but coverage shows them exercised through
`create_app` (`routes/restart.py` 100%, `routes/work.py` 97%). Import-graph heuristics were wrong
here; the measurement corrected them.

## 8. Recommendations

| Item | Recommendation | State |
|---|---|---|
| 12 structurally similar groups (§3) | keep — different inputs, proven non-equivalent | closed |
| Two blind removal tests (§4) | strengthen | **done** |
| 49 tests hidden from direct invocation (§4) | move the `__main__` guard | **done** |
| `test_read_only_starts_are_accepted`, `test_storage_failure…` (§4) | keep as-is | closed |
| `importer.plan()` (§7) | add regression test | **done** |
| `work/export.py`, `bench/ticker.py`, `deeplinks.py` (§7) | add later | open |
| Hardcoded path in `smoke_api_postgres.py` (§6) | fix at open-source readiness | open |
| `\b` inert in `process.py:34` (§3) | production observation; no behaviour change now | open |
| PostgreSQL grants, SSH, Ansible, systemd, browser (§6) | onsite only | see `docs/TODO.md` |

No test was deleted. No `src/tms` source was changed.
