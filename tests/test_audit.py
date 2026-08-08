"""Tests for audit enforcement.

The invariants, and what breaking each one would cost:

* AU1 - an action that cannot be audited must not run. Breaking this produces
  unrecorded production kills, which is precisely the gap FR-AUDIT-ACTION exists
  to close.
* AU2 - a blank reason is a 400, whitespace included.
* AU3 - no UPDATE or DELETE path exists in the source at all.
* AU4 - the actor is the human, never the tms-svc service account.
* AU5 - failures and refusals are recorded too.
"""

import os
import pathlib
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.audit import (  # noqa: E402
    ACTION_AUDIT_EXPORT,
    ACTION_QUERY_KILL,
    ALLOWED_ACTION_TYPES,
    FAILURE,
    MAX_REASON_LENGTH,
    SUCCESS,
    TARGET_QUERY,
    AuditGuard,
    AuditUnavailable,
    InMemoryAuditRepository,
    InvalidActionType,
    ReasonRequired,
    normalise_reason,
)
ACTOR = dict(actor="syhcho", roles=["operator"])

# Assembled from fragments so this scanner does not match its own source when it
# walks the tree - the first version of this check flagged itself.
_APPEND_ONLY_TABLES = ("audit" + "_action", "health" + "_event")
_MUTATING_VERBS = ("upd" + "ate", "del" + "ete from", "trun" + "cate")


def find_mutation_statements(source: str) -> list:
    """Return any mutating statement against an append-only table (AU3).

    Database grants are the real enforcement; this catches the mistake in review
    instead of at 3am against a table nobody can repair.
    """
    lowered = " ".join(source.lower().split())
    offenders = []
    for table in _APPEND_ONLY_TABLES:
        for verb in _MUTATING_VERBS:
            if "{} {}".format(verb, table) in lowered:
                offenders.append("{} {}".format(verb, table))
    return offenders


def guard(writable=True):
    repository = InMemoryAuditRepository(writable=writable)
    return AuditGuard(repository), repository


def kill_action(g, reason="resource hog", **kwargs):
    params = dict(
        action_type=ACTION_QUERY_KILL,
        target_kind=TARGET_QUERY,
        target_id="20260806_1_abc",
        reason=reason,
        target_cluster="prod-a",
    )
    params.update(ACTOR)
    params.update(kwargs)
    return g.action(**params)


class ReasonTest(unittest.TestCase):
    def test_blank_reasons_are_rejected(self):
        for value in (None, "", "   ", "\t\n  "):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ReasonRequired):
                    normalise_reason(value)

    def test_newlines_are_flattened(self):
        """The reason is shown to the user whose query was killed."""
        self.assertEqual(normalise_reason("line1\nline2\t  x"), "line1 line2 x")

    def test_long_reasons_are_capped(self):
        result = normalise_reason("x" * 5000)
        self.assertEqual(len(result), MAX_REASON_LENGTH)
        self.assertTrue(result.endswith("..."))

    def test_action_rejects_blank_reason_before_touching_storage(self):
        """A malformed request must be a 400, not a 503."""
        g, repository = guard(writable=False)
        with self.assertRaises(ReasonRequired):
            kill_action(g, reason="   ")
        self.assertEqual(repository.records, [])


class AU1AvailabilityTest(unittest.TestCase):
    def test_unwritable_store_refuses_the_action(self):
        g, _ = guard(writable=False)
        with self.assertRaises(AuditUnavailable):
            kill_action(g)

    def test_refusal_happens_before_the_action_body_runs(self):
        """The whole point: the kill must not be attempted."""
        g, _ = guard(writable=False)
        performed = []
        try:
            with kill_action(g):
                performed.append(True)
        except AuditUnavailable:
            pass
        self.assertEqual(performed, [], "the action ran despite audit being unavailable")

    def test_no_bypass_parameter_exists(self):
        import inspect

        from tms.core.audit import AuditGuard as Guard

        signature = inspect.signature(Guard.action)
        for forbidden in ("force", "skip_audit", "bypass", "no_audit"):
            self.assertNotIn(forbidden, signature.parameters)


class OutcomeTest(unittest.TestCase):
    def test_success_is_recorded(self):
        g, repository = guard()
        with kill_action(g):
            pass
        self.assertEqual(len(repository.records), 1)
        record = repository.records[0]
        self.assertEqual(record.outcome, SUCCESS)
        self.assertEqual(record.actor, "syhcho")
        self.assertIsNotNone(record.occurred_at)

    def test_failure_is_recorded_and_the_error_propagates(self):
        g, repository = guard()
        with self.assertRaises(RuntimeError):
            with kill_action(g):
                raise RuntimeError("trino said no")
        record = repository.records[0]
        self.assertEqual(record.outcome, FAILURE)
        self.assertIn("trino said no", record.error_message)

    def test_outcome_is_never_left_at_its_placeholder(self):
        """The record starts as FAILURE so a bug cannot mint a false SUCCESS."""
        g, repository = guard()
        with kill_action(g):
            pass
        self.assertEqual(repository.records[0].outcome, SUCCESS)

    def test_exactly_one_record_per_action(self):
        g, repository = guard()
        with kill_action(g):
            pass
        self.assertEqual(len(repository.records), 1)

    def test_details_collected_in_the_block_are_stored(self):
        g, repository = guard()
        with kill_action(g) as audited:
            audited.details["trino_status"] = 200
        self.assertEqual(repository.records[0].details, {"trino_status": 200})

    def test_request_id_is_generated_and_stable(self):
        g, repository = guard()
        with kill_action(g) as audited:
            observed = audited.request_id
        self.assertEqual(repository.records[0].request_id, observed)

    def test_storage_failure_after_the_action_does_not_mask_success(self):
        """The kill already happened; raising here would be a lie."""

        class FailingRepository(InMemoryAuditRepository):
            def write(self, record):
                raise RuntimeError("disk full")

        g = AuditGuard(FailingRepository())
        with g.action(
            action_type=ACTION_QUERY_KILL,
            target_kind=TARGET_QUERY,
            target_id="q",
            reason="why",
            **ACTOR
        ):
            pass  # must not raise


class ActionTypeTest(unittest.TestCase):
    def test_unknown_action_type_is_rejected(self):
        g, _ = guard()
        with self.assertRaises(InvalidActionType):
            g.action(
                action_type="DROP_EVERYTHING",
                target_kind=TARGET_QUERY,
                target_id="x",
                reason="why",
                **ACTOR
            )

    def test_catalogue_matches_the_database_check_constraint(self):
        """Code and database must agree on the whitelist.

        The constraint is defined in 001 and may be amended by a later
        migration, so the effective definition is the last one in migration
        order - reading only 001 would go stale the first time an action type
        is added, which is exactly what happened when CLUSTER_RESTART arrived.
        """
        import re

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        migrations = sorted(
            pathlib.Path(repo_root, "migrations").glob("*.sql"))
        self.assertTrue(migrations, "no migrations found")

        effective = None
        source = None
        pattern = re.compile(
            r"CONSTRAINT\s+audit_action_type_valid\s+CHECK\s*\((.*?)\)\s*;?",
            re.S | re.I)
        for path in migrations:
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                effective, source = match.group(1), path.name

        self.assertIsNotNone(
            effective, "audit_action_type_valid is not defined in any migration")
        for action_type in ALLOWED_ACTION_TYPES:
            self.assertIn(
                "'{}'".format(action_type), effective,
                "{} is in ALLOWED_ACTION_TYPES but missing from the CHECK "
                "constraint (effective definition in {})".format(action_type, source),
            )


class RepositoryContractTest(unittest.TestCase):
    """Implementations must agree on search().

    The in-memory repository once accepted **filters and treated `limit` as a
    column name, so every search returned nothing - silently, because an empty
    audit result looks exactly like "no matching actions".
    """

    def test_search_signatures_match(self):
        import inspect

        from tms.core.audit import AuditRepository

        expected = set(inspect.signature(AuditRepository.search).parameters)
        actual = set(inspect.signature(InMemoryAuditRepository.search).parameters)
        self.assertEqual(expected, actual)

    def test_limit_is_honoured_not_treated_as_a_filter(self):
        repository = InMemoryAuditRepository()
        for index in range(5):
            with AuditGuard(repository).action(
                action_type=ACTION_QUERY_KILL,
                target_kind=TARGET_QUERY,
                target_id="q{}".format(index),
                reason="cleanup",
                **ACTOR
            ):
                pass
        self.assertEqual(len(repository.search(limit=3)), 3)
        self.assertEqual(len(repository.search()), 5)

    def test_results_are_newest_first(self):
        repository = InMemoryAuditRepository()
        for index in range(3):
            with AuditGuard(repository).action(
                action_type=ACTION_QUERY_KILL,
                target_kind=TARGET_QUERY,
                target_id="q{}".format(index),
                reason="cleanup",
                **ACTOR
            ):
                pass
        ids = [r.target_id for r in repository.search()]
        self.assertEqual(ids[0], "q2")


class RefusalTest(unittest.TestCase):
    def test_refusals_are_recorded(self):
        """AU5: 'why did nothing happen?' is an audit question."""
        g, repository = guard()
        g.record_refusal(
            action_type=ACTION_QUERY_KILL,
            target_kind=TARGET_QUERY,
            target_id="q1",
            reason="wanted to kill it",
            error_message="403: viewer role cannot kill queries",
            **ACTOR
        )
        record = repository.records[0]
        self.assertEqual(record.outcome, FAILURE)
        self.assertIn("403", record.error_message)

    def test_refusal_without_a_reason_still_records(self):
        g, repository = guard()
        g.record_refusal(
            action_type=ACTION_QUERY_KILL,
            target_kind=TARGET_QUERY,
            target_id="q1",
            reason=None,
            error_message="403",
            **ACTOR
        )
        self.assertEqual(len(repository.records), 1)
        self.assertIn("not supplied", repository.records[0].reason)

    def test_refusal_storage_failure_does_not_mask_the_original_error(self):
        class FailingRepository(InMemoryAuditRepository):
            def write(self, record):
                raise RuntimeError("disk full")

        g = AuditGuard(FailingRepository())
        self.assertIsNone(
            g.record_refusal(
                action_type=ACTION_AUDIT_EXPORT,
                target_kind="cluster",
                target_id="*",
                reason="export",
                error_message="403",
                **ACTOR
            )
        )


class AppendOnlySourceTest(unittest.TestCase):
    """AU3 enforced in code, not only by database grants."""

    def _read(self, *parts):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_no_mutation_statements_in_the_audit_repository(self):
        offenders = find_mutation_statements(
            self._read("src", "tms", "core", "audit_postgres.py")
        )
        self.assertEqual(offenders, [], "audit_action must never be updated or deleted")

    def test_no_mutation_statements_anywhere_in_the_package(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for root, _dirs, files in os.walk(os.path.join(repo_root, "src")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    offenders = find_mutation_statements(handle.read())
                self.assertEqual(
                    offenders, [], "{} mutates an append-only table".format(path)
                )

    def test_the_scanner_actually_detects_a_violation(self):
        """A scanner that never fires is worse than none - it grants false calm."""
        self.assertTrue(find_mutation_statements("UPDATE audit_action SET reason='x'"))
        self.assertTrue(find_mutation_statements("delete   from   health_event"))
        self.assertEqual(find_mutation_statements("SELECT * FROM audit_action"), [])

    def test_grants_never_give_update_or_delete_on_append_only_tables(self):
        grants = self._read("migrations", "002_grants.sql")
        for line in grants.splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("GRANT"):
                continue
            if "audit_action" in stripped or "health_event" in stripped:
                self.assertNotIn("UPDATE", stripped.upper(), stripped)
                self.assertNotIn("DELETE", stripped.upper(), stripped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
